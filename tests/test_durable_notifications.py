import datetime
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from DurableNotifications import DurableNotificationDispatcher, OUTBOX
from EfficientFirestore import EfficientFirestoreHandler
from LocalState import LocalState
from main import run
from test_features import nba, NOW


class DurableTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = os.path.join(self.folder.name, 'state.sqlite3')
        self.sent = []

    def dispatcher(self, send=None):
        value = DurableNotificationDispatcher(self.path, send=send or (lambda m: self.sent.append(m) or 'message-id'))
        self.addCleanup(value.store.connection.close)
        return value

    def test_restart_between_every_poll_does_not_duplicate_delivery(self):
        for index, phase in enumerate(['scheduled', 'live', 'live', 'halftime', 'live', 'final', 'final']):
            current = self.dispatcher()
            now = NOW + datetime.timedelta(minutes=index)
            current.observe([nba(phase)], now)
            current.dispatch(now)
        self.assertEqual([m.data['milestone'] for m in self.sent], ['start', 'halftime', 'final'])
        self.assertEqual(len(self.dispatcher().store.entries(OUTBOX)), 3)

    def test_firestore_outage_does_not_block_live_to_final_or_health(self):
        dispatcher = self.dispatcher()
        game = Mock(now=NOW)
        game.getNPBLiveInfo.return_value = ([], [])
        handler = Mock()
        handler.publish.side_effect = RuntimeError('429 Quota exceeded')
        health = os.path.join(self.folder.name, 'health.json')
        with patch.dict(os.environ, {'SCOREBOARD_HEALTH_PATH': health}):
            for i, phase in enumerate(['scheduled', 'live', 'final']):
                game.now = NOW + datetime.timedelta(minutes=i)
                game.getNBALiveInfo.return_value = ([], [nba(phase)])
                self.assertEqual(run(game, handler, notifier=dispatcher), 1)
        self.assertEqual([m.data['milestone'] for m in self.sent], ['start', 'final'])
        self.assertTrue(os.path.exists(health))

    def test_initial_live_and_stale_gap_do_not_replay_missed_notifications(self):
        d = self.dispatcher()
        d.observe([nba('live')], NOW)
        d.dispatch(NOW)
        later = NOW + datetime.timedelta(minutes=11)
        d.observe([nba('final')], later)
        d.dispatch(later)
        self.assertEqual(self.sent, [])

    def test_crash_after_claim_is_never_retried(self):
        def crash(message):
            raise SystemExit('killed after FCM request')
        d = self.dispatcher(crash)
        d.observe([nba()], NOW)
        d.observe([nba('live')], NOW)
        with self.assertRaises(SystemExit): d.dispatch(NOW)
        self.dispatcher().dispatch(NOW)
        self.assertEqual(self.sent, [])
        self.assertEqual(d.store.entries(OUTBOX)[0][1]['status'], 'sending')

    def test_mirror_failure_retains_dirty_records_and_does_not_resend(self):
        d = self.dispatcher()
        d.observe([nba()], NOW)
        d.observe([nba('live')], NOW)
        d.dispatch(NOW)
        db = Mock()
        db.batch.return_value.commit.side_effect = RuntimeError('quota')
        with self.assertRaises(RuntimeError): d.mirror(db)
        db.batch.return_value.commit.side_effect = None
        self.assertEqual(d.mirror(db), 2)
        self.assertEqual(d.mirror(db), 0)
        values = [call.args[1] for call in db.batch.return_value.set.call_args_list]
        self.assertTrue(all(x['status'] == 'sent' for x in values if 'payload' in x))
        d.dispatch(NOW)
        self.assertEqual(len(self.sent), 1)
        d.observe([nba('live')], NOW + datetime.timedelta(minutes=1))
        self.assertEqual(d.mirror(db), 0)

    def test_pending_survives_restart_and_expires_without_sending(self):
        d = self.dispatcher()
        d.observe([nba()], NOW)
        d.observe([nba('live')], NOW)
        result = self.dispatcher().dispatch(NOW + datetime.timedelta(minutes=6))
        self.assertEqual(result['expired'], 1)
        self.assertEqual(self.sent, [])


class EfficientTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = os.path.join(self.folder.name, 'cache.sqlite3')
        self.handler = EfficientFirestoreHandler.__new__(EfficientFirestoreHandler)
        self.handler.store = LocalState(self.path)
        self.addCleanup(self.handler.store.connection.close)
        self.handler.db = Mock()
        self.handler.db.collection.return_value.stream.return_value = []

    def test_unchanged_poll_reads_and_writes_nothing_after_restart(self):
        h = self.handler
        row = dict(nba(), update_date=NOW)
        h.publish({'NBA': ([], [row])}, {}, NOW)
        h.db.reset_mock()
        h.store.connection.close()
        h.store = LocalState(self.path)
        self.addCleanup(h.store.connection.close)
        for i in range(1, 5):
            later = NOW + datetime.timedelta(minutes=i)
            h.publish({'NBA': ([], [dict(row, update_date=later)])}, {}, later)
        h.db.collection.assert_not_called()
        h.db.batch.return_value.commit.assert_not_called()
        later = NOW + datetime.timedelta(minutes=5)
        h.publish({'NBA': ([], [dict(row, update_date=later)])}, {}, later)
        h.db.batch.return_value.commit.assert_called_once()
        self.assertEqual(h.db.batch.return_value.set.call_count, 1)
        value = next(iter(h.snapshot('TB_ALL_LIVE_SCORE', later).values()))
        self.assertEqual(value['update_date'], later)
        h.db.reset_mock()
        later = NOW + datetime.timedelta(minutes=15)
        h.publish({'NBA': ([], [dict(row, update_date=later)])}, {}, later)
        self.assertEqual(h.db.batch.return_value.set.call_count, 2)  # score freshness + health

    def test_changed_score_updates_only_its_document(self):
        h = self.handler
        h.publish({'NBA': ([], [nba()])}, {}, NOW)
        h.db.reset_mock()
        h.publish({'NBA': ([], [dict(nba(), homeTeamScore='51')])}, {}, NOW + datetime.timedelta(minutes=5))
        self.assertEqual(h.db.batch.return_value.set.call_count, 1)
        h.db.batch.return_value.delete.assert_not_called()

    def test_notifications_continue_each_minute_while_score_writes_wait_five_minutes(self):
        h = self.handler
        sent = []
        notifier = DurableNotificationDispatcher(self.path, send=lambda m: sent.append(m) or 'sent')
        self.addCleanup(notifier.store.connection.close)
        h.publish({'NBA': ([], [nba()])}, {}, NOW)
        notifier.observe([nba()], NOW)
        h.db.reset_mock()
        for minute, phase in enumerate(['live', 'live', 'halftime', 'final'], 1):
            now = NOW + datetime.timedelta(minutes=minute)
            notifier.observe([nba(phase)], now)
            notifier.dispatch(now)
            h.publish({'NBA': ([], [nba(phase)])}, {}, now)
        self.assertEqual([m.data['milestone'] for m in sent], ['start', 'halftime', 'final'])
        h.db.batch.return_value.commit.assert_not_called()
        h.publish({'NBA': ([], [nba('final')])}, {}, NOW + datetime.timedelta(minutes=5))
        h.db.batch.return_value.commit.assert_called_once()

    def test_failed_commit_keeps_acknowledged_cache_and_backs_off(self):
        h = self.handler
        h.publish({'NBA': ([], [nba()])}, {}, NOW)
        h.db.batch.return_value.commit.side_effect = RuntimeError('429 quota')
        later = NOW + datetime.timedelta(minutes=5)
        with self.assertRaises(RuntimeError):
            h.publish({'NBA': ([], [dict(nba(), homeTeamScore='51')])}, {}, later)
        self.assertEqual(next(iter(h.snapshot('TB_ALL_LIVE_SCORE', later).values()))['homeTeamScore'], '50')
        h.db.reset_mock()
        with self.assertRaises(RuntimeError):
            h.publish({'NBA': ([], [nba()])}, {}, later + datetime.timedelta(minutes=1))
        h.db.collection.assert_not_called()
        h.db.batch.assert_not_called()

    def test_failed_league_preserved_and_empty_success_clears_only_its_matches(self):
        h = self.handler
        other = dict(nba(), league='EPL', matchId='other', homeTeamName='EPL:1')
        h.publish({'NBA': ([], [nba()]), 'EPL': ([], [other])}, {}, NOW)
        later = NOW + datetime.timedelta(minutes=5)
        h.publish({'NBA': ([], [])}, {'EPL': 'offline'}, later)
        self.assertEqual(list(h.snapshot('TB_ALL_LIVE_SCORE', later).values()), [other])
