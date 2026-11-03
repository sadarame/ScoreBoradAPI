"""Publish changed documents only, caching acknowledged remote snapshots on disk.

The single Cron writer owns score/stat collections. Refresh every six hours to
reconcile external changes. A failed commit never advances the local snapshot.
"""
import datetime
import hashlib
from FirestoreHandler import FirestoreHandler, merge_scores
from LocalState import LocalState


def content(value):
    if isinstance(value, dict):
        return {key: content(item) for key, item in value.items()
                if key not in ('update_date', 'updatedAt', 'lastAttemptAt', 'lastSuccessAt')}
    if isinstance(value, list):
        return [content(item) for item in value]
    return value


class EfficientFirestoreHandler(FirestoreHandler):
    def __init__(self, path):
        super().__init__()
        self.store = LocalState(path)

    def defer(self, now):
        with self.store.connection:
            self.store.put('publication', 'retryAt', now + datetime.timedelta(minutes=15))

    def ready(self, now):
        retry = self.store.get('publication', 'retryAt')
        return retry is None or now >= retry

    def snapshot(self, name, now):
        saved = self.store.get('snapshots', name)
        if saved and now - saved['checkedAt'] < datetime.timedelta(hours=6):
            return saved['docs']
        docs = {d.id: d.to_dict() for d in self.db.collection(name).stream(retry=None, timeout=8)}
        with self.store.connection:
            self.store.put('snapshots', name, dict(checkedAt=now, docs=docs))
        return docs

    def commit(self, replacements, now, heartbeat=None):
        heartbeat = heartbeat or {}
        batch, count, saved = self.db.batch(), 0, {}
        for name, desired in replacements.items():
            previous = self.snapshot(name, now)
            acknowledged = dict(previous)
            last_write = self.store.get('writes', name)
            force = name in heartbeat and (last_write is None or now - last_write >= datetime.timedelta(minutes=heartbeat[name]))
            for key in previous.keys() - desired.keys():
                batch.delete(self.db.collection(name).document(key))
                acknowledged.pop(key)
                count += 1
            for key, value in desired.items():
                if force or key not in previous or content(value) != content(previous[key]):
                    batch.set(self.db.collection(name).document(key), value)
                    acknowledged[key] = value
                    count += 1
            saved[name] = acknowledged
        if count > 450:
            raise RuntimeError('Publication exceeds atomic batch limit')
        if count:
            batch.commit(retry=None, timeout=8)
            with self.store.connection:
                for name, docs in saved.items():
                    snapshot = self.store.get('snapshots', name)
                    self.store.put('snapshots', name, dict(checkedAt=snapshot['checkedAt'], docs=docs))
                    # Heartbeat clocks only advance when that collection was due.
                    if name not in heartbeat or self.store.get('writes', name) is None or now - self.store.get('writes', name) >= datetime.timedelta(minutes=heartbeat[name]):
                        self.store.put('writes', name, now)
        return count

    def publish(self, updates, failures, now):
        if not updates:
            raise RuntimeError('All sources failed; existing scores retained')
        if not self.ready(now):
            raise RuntimeError('Firestore publication deferred after failure')
        last = self.store.get('publication', 'scoresAt')
        if last and now - last < datetime.timedelta(minutes=5):
            return
        try:
            previous = self.snapshot('TB_ALL_LIVE_SCORE', now)
            # Team master is needed only for legacy rows without an explicit league.
            teams = list(self.snapshot('TB_TEAM_MST', now).values()) if any(not r.get('league') for r in previous.values()) else []
            merged = merge_scores(list(previous.values()), updates, teams)
            desired = {}
            for row in merged:
                identity = '{}:{}:{}:{}'.format(row.get('league', ''), row.get('matchId', ''), row.get('homeTeamName', ''), row.get('awayTeamName', ''))
                desired[hashlib.sha256(identity.encode()).hexdigest()] = row
            replacements = {'TB_ALL_LIVE_SCORE': desired}
            for league, (legacy, _) in updates.items():
                if league in ('NBA', 'NPB'):
                    replacements['TB_' + league + '_LIVE_SCORE'] = {str(i): row for i, row in enumerate(legacy)}
            status = dict(self.snapshot('TB_SCORE_UPDATE_STATUS', now))
            for league, (_, rows) in updates.items():
                status[league] = dict(lastAttemptAt=now, lastSuccessAt=now, status='ok', count=len(rows))
            for league, error in failures.items():
                status[league] = dict(status.get(league, {}), lastAttemptAt=now, status='error', error=error)
            replacements['TB_SCORE_UPDATE_STATUS'] = status
            self.commit(replacements, now, heartbeat={'TB_SCORE_UPDATE_STATUS': 15, 'TB_ALL_LIVE_SCORE': 5})
            with self.store.connection:
                self.store.put('publication', 'scoresAt', now)
        except Exception:
            self.defer(now)
            raise

    def publish_team_stats(self, updates, now):
        if not self.ready(now):
            return
        last = self.store.get('publication', 'statsAt')
        if last and now - last < datetime.timedelta(minutes=5):
            return
        try:
            previous = self.snapshot('TB_TEAM_STATS', now)
            desired, current = dict(previous), {}
            for league, (_, rows) in updates.items():
                for row in sorted(rows, key=lambda r: r.get('startsAt') or now):
                    for side in ('home', 'away'):
                        code = row[side + 'TeamName']
                        key = hashlib.sha256((league + ':' + code).encode()).hexdigest()
                        data = dict(league=league, searchNM=code, updatedAt=now,
                                    teamName=row.get(side + 'DisplayName', code), side=side)
                        if row.get(side + 'Record'):
                            data['record'] = row[side + 'Record']
                        if row.get(side + 'Form'):
                            data['form'] = row[side + 'Form']
                        if row.get('phase') != 'scheduled':
                            data['lastMatch'] = row
                        current.setdefault(key, {}).update(data)
            for key, data in current.items():
                desired[key] = dict(previous.get(key, {}), **data)
            self.commit({'TB_TEAM_STATS': desired}, now)
            with self.store.connection:
                self.store.put('publication', 'statsAt', now)
        except Exception:
            self.defer(now)
            raise
