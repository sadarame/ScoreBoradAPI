import hashlib
import os
from firebase_admin import credentials, firestore, initialize_app


def merge_scores(existing, updates, teams):
    """Replace successful leagues only; retain last known scores on failures."""
    codes = {league: set() for league in updates}
    for t in teams:
        if t.get('league') in codes:
            codes[t['league']].add(t.get('searchNM'))
    result = []
    for old in existing:
        league = old.get('league')
        if not league:
            league = next((key for key, names in codes.items()
                           if old.get('homeTeamName') in names and old.get('awayTeamName') in names), None)
        if league not in updates:
            result.append(old)
    for legacy, rows in updates.values():
        result.extend(rows)
    return result


class FirestoreHandler:
    _app_initialized = False

    def __init__(self):
        path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'scoreboard-651cf-firebase-adminsdk-obk22-368b90d493.json')
        if not FirestoreHandler._app_initialized:
            initialize_app(credentials.Certificate(path))
            FirestoreHandler._app_initialized = True
        self.db = firestore.client()

    def publish(self, updates, failures, now):
        if not updates:
            raise RuntimeError('All sources failed; existing scores retained')
        all_ref = self.db.collection('TB_ALL_LIVE_SCORE')
        previous = list(all_ref.stream())
        teams = [d.to_dict() for d in self.db.collection('TB_TEAM_MST').stream()]
        merged = merge_scores([d.to_dict() for d in previous], updates, teams)
        replacements = [(all_ref, previous, merged)]
        for league, (legacy, rows) in updates.items():
            ref = self.db.collection('TB_' + league + '_LIVE_SCORE')
            replacements.append((ref, list(ref.stream()), legacy))
        writes = sum(len(docs) + len(rows) for _, docs, rows in replacements) + len(updates) + len(failures)
        if writes > 450:
            raise RuntimeError('Publication exceeds safe batch size; existing scores retained')
        batch = self.db.batch()
        for ref, docs, rows in replacements:
            for doc in docs:
                batch.delete(doc.reference)
            for i, data in enumerate(rows):
                identity = '{}:{}:{}'.format(data.get('league', ''), data.get('matchId', ''), i)
                key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
                batch.set(ref.document(key), data)
        for league, (_, rows) in updates.items():
            batch.set(self.db.collection('TB_SCORE_UPDATE_STATUS').document(league),
                      dict(lastAttemptAt=now, lastSuccessAt=now, status='ok', count=len(rows)))
        for league, error in failures.items():
            batch.set(self.db.collection('TB_SCORE_UPDATE_STATUS').document(league),
                      dict(lastAttemptAt=now, status='error', error=error), merge=True)
        batch.commit()
