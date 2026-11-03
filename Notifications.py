"""Multi-sport milestone notifications with persistent event claims (single Cron host).

Never replay historical milestones on initial deployment. Ambiguous send failures
are retained for operator inspection rather than automatically sent twice.
"""
import datetime
import hashlib
import re
from firebase_admin import firestore, messaging
from SportsData import JST


SUPPORTED_LEAGUES = frozenset(['NBA', 'NPB', 'J1', 'EPL', 'LALIGA',
                              'BUNDESLIGA', 'SERIEA', 'LIGUE1'])
EVENTS = frozenset(['start', 'halftime', 'final'])


def topic(team, milestone, league='NBA'):
    if league not in SUPPORTED_LEAGUES or milestone not in EVENTS:
        raise ValueError('Unsupported notification topic')
    if not isinstance(team, str) or not team:
        raise ValueError('Missing team code')
    # Reserve the lowercase u prefix so a literal safe code cannot collide
    # with an encoded Japanese name or league-scoped soccer code.
    code = team if re.fullmatch(r'[A-Za-z0-9_-]+', team) and not team.startswith('u') else 'u' + team.encode('utf-8').hex()
    value = 'score_{}_{}_{}'.format(league, code, milestone)
    if len(value) > 900:
        raise ValueError('Notification topic too long')
    return value


def _aware(value):
    return isinstance(value, datetime.datetime) and value.tzinfo is not None


def _period(row):
    try:
        return int(row.get('period', 0))
    except (TypeError, ValueError):
        return 0


def _past_midpoint(row):
    league, period = row.get('league'), _period(row)
    if row.get('phase') in ('halftime', 'final'):
        return True
    return (league == 'NPB' and period >= 6 or
            league == 'NBA' and period >= 3 or
            league in SUPPORTED_LEAGUES - {'NBA', 'NPB'} and period >= 2)


def milestone(previous, current, now):
    league = current.get('league')
    if not previous or league not in SUPPORTED_LEAGUES:
        return None
    seen = previous.get('reliableObservedAt', previous.get('observedAt'))
    if not _aware(seen) or now < seen or now - seen > datetime.timedelta(minutes=10):
        return None
    if previous.get('hasFinished'):
        return None
    old = previous.get('notificationPhase', previous.get('phase'))
    new, period = current.get('phase'), _period(current)
    if old == 'scheduled' and new == 'live' and not previous.get('hasStarted'):
        # Do not infer a start after already reaching later play or a restart.
        if period == 1 and (league != 'NPB' or current.get('inningHalf') == 'top'):
            return 'start'
    if (league == 'NPB' and previous.get('phase') == 'live' and new == 'live'
            and _period(previous) == 5 and previous.get('inningHalf') == 'bottom'
            and period == 6 and current.get('inningHalf') == 'top'
            and not previous.get('midpointPassed')):
        return 'halftime'
    if old == 'live' and new == 'halftime' and not previous.get('midpointPassed'):
        if league == 'NPB':
            return 'halftime' if period == 5 and current.get('fifthInningComplete') is True else None
        if league == 'NBA' or period in (1, 2):
            return 'halftime'
    if old in ('live', 'halftime') and new == 'final':
        return 'final'
    return None


def game_key(row):
    league = row.get('league')
    if league not in SUPPORTED_LEAGUES:
        raise ValueError('Unsupported league')
    if league == 'NBA':
        # Preserve old hashes across NBA CDN/ESPN source changes.
        if not _aware(row.get('startsAt')):
            raise ValueError('Missing start time')
        date = row['startsAt'].astimezone(JST).date().isoformat()
        identity = '{}:{}:{}:{}'.format(league, date, row['homeTeamName'], row['awayTeamName'])
    else:
        match_id = str(row.get('matchId') or '').strip()
        if not match_id:
            raise ValueError('Missing match ID')
        if league == 'NPB':
            # Yahoo's index/top URLs identify the same game. Never reduce
            # identity to teams+date: doubleheaders have distinct game IDs.
            found = re.search(r'/game/([A-Za-z0-9_-]+)(?:/|$)', match_id)
            match_id = found.group(1) if found else match_id
        identity = '{}:{}:{}:{}'.format(league, match_id, row['homeTeamName'], row['awayTeamName'])
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


NBA_NAMES = {'ATL': 'ホークス', 'BOS': 'セルティックス', 'BKN': 'ネッツ', 'CHA': 'ホーネッツ', 'CHI': 'ブルズ', 'CLE': 'キャバリアーズ', 'DAL': 'マーベリックス', 'DEN': 'ナゲッツ', 'DET': 'ピストンズ', 'GSW': 'ウォリアーズ', 'HOU': 'ロケッツ', 'IND': 'ペイサーズ', 'LAC': 'クリッパーズ', 'LAL': 'レイカーズ', 'MEM': 'グリズリーズ', 'MIA': 'ヒート', 'MIL': 'バックス', 'MIN': 'ティンバーウルブズ', 'NOP': 'ペリカンズ', 'NYK': 'ニックス', 'OKC': 'サンダー', 'ORL': 'マジック', 'PHI': 'シクサーズ', 'PHX': 'サンズ', 'POR': 'トレイルブレイザーズ', 'SAC': 'キングス', 'SAS': 'スパーズ', 'TOR': 'ラプターズ', 'UTA': 'ジャズ', 'WAS': 'ウィザーズ', 'CHO': 'ホーネッツ', 'BRK': 'ネッツ'}
LEAGUE_NAMES = {'NBA': 'NBA', 'NPB': 'プロ野球', 'J1': 'J1リーグ', 'EPL': 'プレミアリーグ', 'LALIGA': 'ラ・リーガ', 'BUNDESLIGA': 'ブンデスリーガ', 'SERIEA': 'セリエA', 'LIGUE1': 'リーグ・アン'}


def message_payload(row, event):
    league = row['league']
    labels = {'start': '試合開始', 'halftime': '5回終了' if league == 'NPB' else 'ハーフタイム', 'final': '試合終了'}
    def display_name(side):
        code = row[side + 'TeamName']
        if league == 'NBA' and code in NBA_NAMES:
            return NBA_NAMES[code]
        return row.get(side + 'DisplayName') or code
    h, a = display_name('home'), display_name('away')
    def score_text(value):
        return '—' if value is None or value == '' else str(value)
    body = '{} {} − {} {}'.format(h, score_text(row.get('homeTeamScore')), score_text(row.get('awayTeamScore')), a)
    if league == 'NPB' and event == 'halftime' and row.get('phase') == 'live':
        body += '\n現在のスコア（6回表）'
    return dict(title=LEAGUE_NAMES[league] + ' · ' + labels[event], body=body,
                condition="'{}' in topics || '{}' in topics".format(topic(row['homeTeamName'], event, league), topic(row['awayTeamName'], event, league)),
                data=dict(matchId=str(row['matchId']), league=league, milestone=event))


def notification_state(current, old, now):
    """The same transition state for Firestore and single-host durable storage."""
    phase = current.get('phase', 'unknown')
    state = dict(phase=phase, period=_period(current), inningHalf=current.get('inningHalf'),
                 observedAt=now, expiresAt=now + datetime.timedelta(days=7))
    for field in ('notificationPhase', 'reliableObservedAt', 'hasStarted', 'midpointPassed', 'hasFinished', 'startsAt', 'gameDate'):
        if old and field in old:
            state[field] = old[field]
    if phase in ('scheduled', 'live', 'halftime', 'final'):
        state.update(notificationPhase=phase, reliableObservedAt=now)
    elif old and 'notificationPhase' not in state:
        state.update(notificationPhase=old.get('phase'), reliableObservedAt=old.get('observedAt'))
    state['hasStarted'] = bool(state.get('hasStarted') or (old or {}).get('phase') in ('live', 'halftime', 'final') or phase in ('live', 'halftime', 'final'))
    state['midpointPassed'] = bool(state.get('midpointPassed') or _past_midpoint(old or {}) or _past_midpoint(current))
    state['hasFinished'] = bool(state.get('hasFinished') or (old or {}).get('phase') == 'final' or phase == 'final')
    for field in ('startsAt', 'gameDate'):
        if current.get(field) is not None:
            state[field] = current[field]
    return state


def delivery_message(payload, event_id, now):
    return messaging.Message(
        notification=messaging.Notification(title=payload['title'], body=payload['body']),
        condition=payload['condition'], data=payload['data'],
        android=messaging.AndroidConfig(priority='high', ttl=datetime.timedelta(minutes=5),
            notification=messaging.AndroidNotification(tag=event_id)),
        apns=messaging.APNSConfig(headers={'apns-collapse-id': hashlib.sha256(event_id.encode('utf-8')).hexdigest(),
            'apns-push-type': 'alert', 'apns-priority': '10',
            'apns-expiration': str(int(now.timestamp()) + 300)},
            payload=messaging.APNSPayload(aps=messaging.Aps(sound='default'))))


class NotificationDispatcher:
    def __init__(self, db, send=messaging.send):
        self.db, self.send = db, send

    def observe(self, rows, now):
        for row in rows:
            if row.get('league') not in SUPPORTED_LEAGUES:
                continue
            try:
                key = game_key(row)
            except (ValueError, KeyError):
                continue
            state_ref = self.db.collection('TB_NOTIFICATION_STATE').document(key)

            @firestore.transactional
            def record(tx):
                snapshot = state_ref.get(transaction=tx)
                old = snapshot.to_dict() if snapshot.exists else None
                if old and _aware(old.get('observedAt')) and now < old['observedAt']:
                    return
                current = dict(row)
                if not _aware(current.get('startsAt')) and old and _aware(old.get('startsAt')):
                    current['startsAt'] = old['startsAt']
                if not current.get('gameDate') and old:
                    current['gameDate'] = old.get('gameDate')
                event = milestone(old, current, now)
                event_ref = self.db.collection('TB_NOTIFICATION_OUTBOX').document(key + '_' + event) if event else None
                existing = event_ref.get(transaction=tx) if event_ref else None
                tx.set(state_ref, notification_state(current, old, now))
                if event_ref and not existing.exists:
                    tx.set(event_ref, dict(status='pending', createdAt=now, payload=message_payload(current, event), expiresAt=now + datetime.timedelta(days=7)))
            record(self.db.transaction())

    def dispatch(self, now):
        result = dict(sent=0, failed=0, expired=0)
        pending = self.db.collection('TB_NOTIFICATION_OUTBOX').where('status', '==', 'pending').limit(100).stream()
        for snapshot in pending:
            ref = snapshot.reference

            @firestore.transactional
            def claim(tx):
                current = ref.get(transaction=tx).to_dict()
                if not current or current['status'] != 'pending':
                    return None
                if now - current['createdAt'] > datetime.timedelta(minutes=5):
                    tx.update(ref, dict(status='expired'))
                    return None
                tx.update(ref, dict(status='sending', claimedAt=now))
                return current['payload']
            payload = claim(self.db.transaction())
            if not payload:
                continue
            try:
                message = delivery_message(payload, snapshot.id, now)
                message_id = self.send(message)
            except Exception as exc:
                ref.update(dict(status='failed', error=type(exc).__name__))
                result['failed'] += 1
                continue
            ref.update(dict(status='sent', messageId=message_id, sentAt=now))
            result['sent'] += 1
        return result
