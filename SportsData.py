"""Shared public-score normalization. Datetimes are stored as UTC, rendered as JST."""
import datetime
import re

UTC = datetime.timezone.utc
JST = datetime.timezone(datetime.timedelta(hours=9))
SOCCER_LEAGUES = {
    'J1': ('jpn.1', 'J1リーグ'),
    'EPL': ('eng.1', 'プレミアリーグ'),
    'LALIGA': ('esp.1', 'ラ・リーガ'),
    'BUNDESLIGA': ('ger.1', 'ブンデスリーガ'),
    'SERIEA': ('ita.1', 'セリエA'),
    'LIGUE1': ('fra.1', 'リーグ・アン'),
}
STAT_LABELS = {
    'rebounds': 'リバウンド', 'totalRebounds': 'リバウンド', 'reboundsTotal': 'リバウンド',
    'assists': 'アシスト', 'steals': 'スティール', 'blocks': 'ブロック',
    'turnovers': 'ターンオーバー', 'turnoversTotal': 'ターンオーバー',
    'fieldGoalPct': 'FG成功率', 'fieldGoalsPercentage': 'FG成功率',
    'threePointFieldGoalPct': '3P成功率', 'threePointersPercentage': '3P成功率',
    'freeThrowPct': 'FT成功率', 'freeThrowsPercentage': 'FT成功率',
    'possessionPct': 'ボール支配率', 'totalShots': 'シュート', 'shotsOnTarget': '枠内シュート',
    'wonCorners': 'コーナーキック', 'foulsCommitted': 'ファウル',
    'yellowCards': 'イエローカード', 'redCards': 'レッドカード',
}


def instant(value):
    if not value:
        return None
    parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp has no timezone')
    return parsed.astimezone(UTC)


def stats(values, nba=False):
    """Only provided metrics; absence is never converted into a zero."""
    if isinstance(values, dict):
        values = [dict(name=k, displayValue=v) for k, v in values.items()]
    result = {}
    for item in values or []:
        key = item.get('name')
        if key not in STAT_LABELS:
            continue
        value = item.get('displayValue')
        if value is None or value == '':
            continue
        if key.endswith('Percentage') and nba:
            value = '{:.1f}'.format(float(value) * 100)
        value = str(value)
        if key.endswith(('Pct', 'Percentage')) and not value.endswith('%'):
            value += '%'
        result[STAT_LABELS[key]] = value
    return result


def npb_metadata(status, game_date):
    """Explicit Yahoo labels only; an inning's end is not the game's end."""
    value = re.sub(r'\s+', '', status)
    result = dict(phase='unknown', period=0, gameDate=game_date.isoformat())
    if any(word in value for word in ('中止', 'ノーゲーム')):
        result['phase'] = 'cancelled'
    elif '延期' in value:
        result['phase'] = 'postponed'
    elif any(word in value for word in ('中断', 'サスペンデッド')):
        result['phase'] = 'suspended'
    elif '試合終了' in value:
        result['phase'] = 'final'
    elif re.fullmatch(r'5回(?:裏)?終了', value):
        result.update(phase='halftime', period=5, inningHalf='bottom', fifthInningComplete=True)
    else:
        inning = re.match(r'(\d{1,2})回(表|裏)', value)
        scheduled = re.fullmatch(r'(\d{1,2}):(\d{2})(?:開始(?:予定)?)?', value)
        if inning and 1 <= int(inning.group(1)) <= 30:
            result.update(phase='live', period=int(inning.group(1)),
                          inningHalf='top' if inning.group(2) == '表' else 'bottom')
        elif re.fullmatch(r'(\d{1,2})回終了', value):
            result.update(phase='live', period=int(re.match(r'\d+', value).group()), inningHalf='bottom')
        elif scheduled and int(scheduled.group(1)) <= 23 and int(scheduled.group(2)) <= 59:
            result.update(phase='scheduled', status='試合前',
                          startsAt=datetime.datetime.combine(game_date,
                              datetime.time(int(scheduled.group(1)), int(scheduled.group(2))), tzinfo=JST))
        elif value in ('試合前', '開始前'):
            result['phase'] = 'scheduled'
    return result


def espn_phase(status):
    kind = status['type']
    name = kind.get('name', '')
    if name in ('STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_CANCELLED', 'STATUS_SUSPENDED', 'STATUS_DELAYED', 'STATUS_ABANDONED'):
        return 'cancelled' if name in ('STATUS_CANCELED', 'STATUS_CANCELLED', 'STATUS_ABANDONED') else 'postponed'
    if kind.get('completed') is True:
        return 'final'
    if name == 'STATUS_HALFTIME':
        return 'halftime'
    if name in ('STATUS_FINAL', 'STATUS_FULL_TIME', 'STATUS_FINAL_AET', 'STATUS_FINAL_PEN'):
        return 'final'
    return {'pre': 'scheduled', 'in': 'live'}.get(kind.get('state'), 'unknown')


def status_text(phase, period=0, clock='', basketball=True):
    fixed = {'scheduled': '試合前', 'halftime': 'ハーフタイム', 'final': '試合終了',
             'postponed': '延期・中断', 'cancelled': '中止', 'unknown': '状況確認中'}
    if phase != 'live':
        return fixed.get(phase, '状況確認中')
    if basketball:
        part = '第{}Q'.format(period) if 0 < period <= 4 else '延長' if period > 4 else '試合中'
        return '{} {}'.format(part, clock).strip()
    return '{} {}'.format('前半' if period == 1 else '後半' if period == 2 else '延長', clock).strip()


def espn_metadata(event, home, away, basketball=True):
    status = event['status']
    phase = espn_phase(status)
    period = int(status.get('period', 0))
    result = dict(startsAt=instant(event['date']), phase=phase, period=period,
                  clock=status.get('displayClock', ''), sport='basketball' if basketball else 'soccer',
                  status=status_text(phase, period, status.get('displayClock', ''), basketball))
    for side, competitor in [('home', home), ('away', away)]:
        result[side + 'DisplayName'] = competitor['team'].get('displayName', competitor['team'].get('abbreviation', ''))
        result[side + 'Stats'] = stats(competitor.get('statistics', [])) if phase != 'scheduled' else {}
        records = competitor.get('records', [])
        record = next((r.get('summary', '') for r in records if r.get('type') == 'total'), '')
        result[side + 'Record'] = record
        result[side + 'Form'] = competitor.get('form', '')
    return result


def nba_metadata(game):
    state = game.get('gameStatus')
    raw = game.get('gameStatusText', '')
    phase = {1: 'scheduled', 2: 'live', 3: 'final'}.get(state, 'unknown')
    if 'half' in raw.lower():
        phase = 'halftime'
    if 'postpon' in raw.lower() or 'delay' in raw.lower():
        phase = 'postponed'
    if 'cancel' in raw.lower():
        phase = 'cancelled'
    period = int(game.get('period', 0))
    clock = game.get('gameClock', '')
    match = re.match(r'PT(\d+)M(\d+)(?:\.\d+)?S', clock)
    if match:
        clock = '{}:{}'.format(match.group(1).zfill(2), match.group(2).zfill(2))
    result = dict(startsAt=instant(game.get('gameTimeUTC')), phase=phase, period=period,
                  clock=clock, sport='basketball')
    # Old fixtures/records can lack structured status; retain them unchanged.
    if phase != 'unknown':
        result['status'] = status_text(phase, period, clock)
    for side in ('home', 'away'):
        team = game[side + 'Team']
        result[side + 'DisplayName'] = ' '.join(filter(None, [team.get('teamCity'), team.get('teamName')])) or team['teamTricode']
        result[side + 'Record'] = '{}-{}'.format(team['wins'], team['losses']) if 'wins' in team and 'losses' in team else ''
        result[side + 'Stats'] = stats(team.get('statistics', {}), nba=True) if phase != 'scheduled' else {}
    return result
