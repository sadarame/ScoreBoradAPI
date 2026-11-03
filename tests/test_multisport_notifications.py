"""Offline synthetic milestone fixtures. No Firebase initialization or send calls."""
import ast
import datetime
import pathlib
import unittest
from unittest.mock import Mock, patch

from Game import Game
from Notifications import NotificationDispatcher, game_key, message_payload, milestone, topic
from SportsData import SOCCER_LEAGUES, espn_metadata, espn_phase, npb_metadata
from main import run
from test_features import DB, nba

NOW = datetime.datetime(2026, 10, 28, 9, tzinfo=datetime.timezone.utc)
DAY = NOW.date()


def npb(status='18:00', game_id='2021039495'):
    row = dict(league='NPB', matchId='/npb/game/{}/index'.format(game_id),
               homeTeamName='阪神', awayTeamName='巨人',
               homeTeamScore='2', awayTeamScore='1', status=status)
    row.update(npb_metadata(status, DAY))
    return row


def soccer(league='J1', phase='scheduled', period=1):
    return dict(league=league, matchId='{}:123'.format(league),
                homeTeamName=league + ':1', awayTeamName=league + ':2',
                homeDisplayName='Home Club', awayDisplayName='Away Club',
                homeTeamScore='1', awayTeamScore='0', phase=phase,
                startsAt=NOW, period=period)


def outbox(db):
    return [value for key, value in db.docs.items() if key.startswith('TB_NOTIFICATION_OUTBOX/')]


class NormalizationTests(unittest.TestCase):
    def test_topic_encoding_is_compatible_safe_and_collision_free(self):
        self.assertEqual(topic('LAL', 'start'), 'score_NBA_LAL_start')
        self.assertEqual(topic('DeNA', 'final', 'NPB'), 'score_NPB_DeNA_final')
        self.assertEqual(topic('阪神', 'halftime', 'NPB'), 'score_NPB_u' + '阪神'.encode('utf-8').hex() + '_halftime')
        self.assertEqual(topic('J1:1', 'start', 'J1'), 'score_J1_u4a313a31_start')
        self.assertNotEqual(topic('J1:1', 'start', 'J1'), topic('u4a313a31', 'start', 'J1'))
        self.assertNotEqual(topic('a:b', 'start', 'J1'), topic('a_b', 'start', 'J1'))
        self.assertNotEqual(topic('A\n', 'start'), topic('A', 'start'))
        for team in ['', None]:
            with self.assertRaises(ValueError): topic(team, 'start')

    def test_topic_900_character_boundary(self):
        self.assertEqual(len(topic('A' * 884, 'start', 'NBA')), 900)
        with self.assertRaises(ValueError):
            topic('A' * 885, 'start', 'NBA')

    def test_npb_explicit_innings_do_not_become_full_time(self):
        for text in ('5回終了', '5回裏終了'):
            row = npb(text)
            self.assertEqual(row['phase'], 'halftime')
            self.assertTrue(row['fifthInningComplete'])
        for text in ('1回表', '5回表終了', '5回裏', '6回表', '9回終了', '12回裏'):
            self.assertEqual(npb(text)['phase'], 'live')
        self.assertEqual(npb('試合終了')['phase'], 'final')
        self.assertEqual(npb('中断')['phase'], 'suspended')
        self.assertEqual(npb('中止')['phase'], 'cancelled')
        self.assertEqual(npb('延期')['phase'], 'postponed')
        self.assertEqual(npb('わからない')['phase'], 'unknown')
        self.assertEqual(npb('試合前')['phase'], 'scheduled')

    def test_npb_parser_adds_metadata_to_actual_html_structure(self):
        fixture = (pathlib.Path(__file__).parent / 'fixtures' / 'npb-games.html').read_text()
        clock = datetime.datetime(2026, 10, 25, 9, tzinfo=datetime.timezone.utc)
        # Status is synthetic; DOM and source game URL are recorded fixtures.
        _, rows = Game(now=clock).parse_npb(fixture.replace('試合終了', '5回終了'))
        self.assertEqual(rows[0]['phase'], 'halftime')
        self.assertEqual(rows[0]['period'], 5)
        self.assertEqual(rows[0]['gameDate'], '2026-10-25')
        self.assertNotIn('startsAt', rows[0])

    def test_npb_identity_survives_start_time_loss_and_url_view_changes(self):
        planned, live = npb(), npb('1回表')
        live['matchId'] = 'https://baseball.yahoo.co.jp/npb/game/2021039495/top'
        self.assertEqual(game_key(planned), game_key(live))
        self.assertNotEqual(game_key(live), game_key(npb('1回表', '2021039496')))
        self.assertEqual(game_key(nba()), game_key(dict(nba(), matchId='different-espn-id')))

    def test_soccer_cancel_unknown_pk_and_extended_time_are_not_false_starts(self):
        self.assertEqual(espn_phase(dict(type=dict(name='STATUS_FINAL_PEN', completed=True, state='post'))), 'final')
        self.assertEqual(espn_phase(dict(type=dict(name='STATUS_ABANDONED', state='post'))), 'cancelled')
        self.assertEqual(espn_phase(dict(type=dict(name='UNRECOGNIZED', state='post'))), 'unknown')
        prior = dict(phase='scheduled', observedAt=NOW)
        for period in (2, 3, 4, 5):
            self.assertIsNone(milestone(prior, soccer(phase='live', period=period), NOW))

    def test_payload_keeps_zero_scores_and_marks_only_missing_values(self):
        value = dict(npb('5回終了'), homeTeamScore=0, awayTeamScore=None)
        self.assertIn('阪神 0 − — 巨人', message_payload(value, 'halftime')['body'])

    def test_all_changed_files_parse_with_python37_grammar(self):
        for path in ('Notifications.py', 'Game.py', 'SportsData.py', 'main.py'):
            ast.parse(pathlib.Path(path).read_text(), filename=path, feature_version=(3, 7))


@patch('Notifications.firestore.transactional', lambda fn: fn)
class DispatchTests(unittest.TestCase):
    def dispatcher(self):
        db, sent = DB(), []
        return db, sent, NotificationDispatcher(db, send=lambda msg: sent.append(msg) or 'mock-message')

    def test_npb_scheduled_live_fifth_final_uses_inherited_start_time(self):
        db, sent, dispatcher = self.dispatcher()
        for i, status in enumerate(['18:00', '1回表', '5回裏', '5回終了', '6回表', '試合終了']):
            dispatcher.observe([npb(status)], NOW + datetime.timedelta(minutes=i))
        self.assertEqual(len(outbox(db)), 3)
        self.assertEqual(sorted(x['payload']['data']['milestone'] for x in outbox(db)), ['final', 'halftime', 'start'])
        mid = next(x for x in outbox(db) if x['payload']['data']['milestone'] == 'halftime')
        self.assertEqual(mid['payload']['title'], 'プロ野球 · 5回終了')
        self.assertEqual(mid['payload']['body'], '阪神 2 − 1 巨人')
        dispatcher.dispatch(NOW + datetime.timedelta(minutes=5))
        dispatcher.dispatch(NOW + datetime.timedelta(minutes=5))
        self.assertEqual(len(sent), 3)

    def test_npb_fifth_bottom_to_sixth_top_is_labelled_detection_score(self):
        db, _, dispatcher = self.dispatcher()
        dispatcher.observe([npb('5回裏')], NOW)
        dispatcher.observe([npb('6回表')], NOW + datetime.timedelta(minutes=1))
        self.assertEqual(len(outbox(db)), 1)
        self.assertIn('現在のスコア（6回表）', outbox(db)[0]['payload']['body'])
        self.assertEqual(outbox(db)[0]['payload']['body'], '阪神 2 − 1 巨人\n現在のスコア（6回表）')

    def test_npb_does_not_guess_midpoint_from_skipped_or_interrupted_play(self):
        for sequence in [['6回表'], ['5回表', '6回表'], ['4回裏', '6回表'], ['5回裏', '6回裏'], ['5回裏', '中断', '6回表']]:
            db, _, dispatcher = self.dispatcher()
            for i, status in enumerate(sequence):
                dispatcher.observe([npb(status)], NOW + datetime.timedelta(minutes=i))
            self.assertEqual(outbox(db), [], sequence)

    def test_npb_doubleheader_events_are_independent(self):
        db, _, dispatcher = self.dispatcher()
        for game_id in ('2021039495', '2021039496'):
            dispatcher.observe([npb('18:00', game_id)], NOW)
            dispatcher.observe([npb('1回表', game_id)], NOW + datetime.timedelta(seconds=30))
        self.assertEqual(len(outbox(db)), 2)

    def test_each_soccer_league_emits_start_half_final_once(self):
        for league in SOCCER_LEAGUES:
            db, sent, dispatcher = self.dispatcher()
            for i, (phase, period) in enumerate([('scheduled', 1), ('live', 1), ('halftime', 1), ('live', 2), ('live', 3), ('live', 5), ('final', 5)]):
                dispatcher.observe([soccer(league, phase, period)], NOW + datetime.timedelta(seconds=i))
            dispatcher.dispatch(NOW + datetime.timedelta(minutes=1))
            dispatcher.dispatch(NOW + datetime.timedelta(minutes=1))
            self.assertEqual(len(sent), 3, league)
            self.assertTrue(all(x['payload']['data']['league'] == league for x in outbox(db)))
            self.assertTrue(all('score_' + league + '_u' in x['payload']['condition'] for x in outbox(db)))

    def test_unknown_and_short_suspension_do_not_lose_final_transition(self):
        for middle in ('unknown', 'postponed', 'suspended', 'cancelled'):
            db, _, dispatcher = self.dispatcher()
            for i, phase in enumerate(['live', middle, 'final']):
                dispatcher.observe([soccer(phase=phase)], NOW + datetime.timedelta(minutes=i))
            self.assertEqual([x['payload']['data']['milestone'] for x in outbox(db)], ['final'])

    def test_initial_baselines_and_late_observations_do_not_replay(self):
        for initial in ['live', 'halftime', 'final']:
            db, _, dispatcher = self.dispatcher()
            dispatcher.observe([soccer(phase=initial)], NOW)
            self.assertEqual(outbox(db), [])
        db, _, dispatcher = self.dispatcher()
        dispatcher.observe([soccer(phase='live')], NOW)
        dispatcher.observe([soccer(phase='unknown')], NOW + datetime.timedelta(minutes=9))
        dispatcher.observe([soccer(phase='final')], NOW + datetime.timedelta(minutes=11))
        self.assertEqual(outbox(db), [])

    def test_state_regressions_cannot_reemit_started_or_midpoint_or_final(self):
        db, _, dispatcher = self.dispatcher()
        for phase in ['halftime', 'scheduled', 'live', 'halftime', 'final', 'scheduled', 'live', 'final']:
            dispatcher.observe([soccer(phase=phase)], NOW)
        self.assertEqual([x['payload']['data']['milestone'] for x in outbox(db)], ['final'])

    def test_cancelled_or_unknown_initial_game_never_looks_like_start(self):
        db, _, dispatcher = self.dispatcher()
        for phase in ('unknown', 'cancelled'):
            db, _, dispatcher = self.dispatcher()
            dispatcher.observe([soccer(phase=phase)], NOW)
            dispatcher.observe([soccer(phase='final')], NOW)
            self.assertEqual(outbox(db), [])

    def test_legacy_nba_state_without_new_fields_keeps_final_detection(self):
        db, _, dispatcher = self.dispatcher()
        row = nba('final')
        key = 'TB_NOTIFICATION_STATE/' + game_key(row)
        db.docs[key] = dict(phase='live', observedAt=NOW)
        dispatcher.observe([row], NOW + datetime.timedelta(seconds=30))
        self.assertEqual([x['payload']['data']['milestone'] for x in outbox(db)], ['final'])
        self.assertTrue(db.docs[key]['hasFinished'])
        dispatcher.observe([nba('live')], NOW + datetime.timedelta(seconds=40))
        dispatcher.observe([row], NOW + datetime.timedelta(seconds=50))
        self.assertEqual(len(outbox(db)), 1)

    def test_old_out_of_order_observation_does_not_replace_new_state(self):
        db, _, dispatcher = self.dispatcher()
        dispatcher.observe([soccer(phase='live')], NOW)
        dispatcher.observe([soccer()], NOW - datetime.timedelta(minutes=1))
        self.assertEqual(db.docs['TB_NOTIFICATION_STATE/' + game_key(soccer())]['phase'], 'live')

    def test_ambiguous_delivery_failure_and_expired_events_are_not_retried(self):
        db, _, dispatcher = self.dispatcher()
        send = Mock(side_effect=TimeoutError())
        dispatcher.send = send
        dispatcher.observe([npb()], NOW)
        dispatcher.observe([npb('1回表')], NOW)
        dispatcher.dispatch(NOW)
        dispatcher.dispatch(NOW)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(outbox(db)[0]['status'], 'failed')
        dispatcher.observe([npb('5回裏')], NOW)
        dispatcher.observe([npb('5回終了')], NOW)
        dispatcher.dispatch(NOW + datetime.timedelta(minutes=6))
        self.assertEqual(send.call_count, 1)
        self.assertTrue(any(x['status'] == 'expired' for x in outbox(db)))


class PipelineTests(unittest.TestCase):
    def test_main_observes_successful_leagues_only(self):
        game = Mock(now=NOW)
        game.getNBALiveInfo.side_effect = ValueError('offline')
        game.getNPBLiveInfo.return_value = ([], [npb()])
        handler, notifier = Mock(), Mock()
        code = run(game, handler, [('J1', lambda: ([], [soccer()]))], notifier)
        self.assertEqual(code, 1)
        observed = notifier.observe.call_args[0][0]
        self.assertEqual([row['league'] for row in observed], ['NPB', 'J1'])
        notifier.dispatch.assert_called_once_with(NOW)

    def test_failed_publication_does_not_block_notification_delivery(self):
        game = Mock(now=NOW)
        game.getNBALiveInfo.return_value = ([], [nba()])
        game.getNPBLiveInfo.return_value = ([], [])
        handler, notifier = Mock(), Mock()
        handler.publish.side_effect = RuntimeError('offline')
        self.assertEqual(run(game, handler, notifier=notifier), 1)
        notifier.observe.assert_called_once()
        notifier.dispatch.assert_called_once_with(NOW)


if __name__ == '__main__':
    unittest.main()
