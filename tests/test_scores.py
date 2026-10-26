import copy
import datetime
import json
import pathlib
import unittest
from unittest.mock import Mock, patch
import requests
from Game import Game, NBA_URL, request, team_code
from FirestoreHandler import FirestoreHandler, merge_scores
from main import run

FIXTURES = pathlib.Path(__file__).parent / 'fixtures'
NOW = datetime.datetime(2026, 10, 26, 5, 0, tzinfo=datetime.timezone.utc)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.espn = json.loads((FIXTURES / 'espn.json').read_text())
        self.game = Game(now=NOW)

    def test_denied_nba_falls_back_and_keeps_app_codes(self):
        def fetch(url):
            if url == NBA_URL:
                raise requests.HTTPError('403')
            self.assertIn('dates=20261025', url)
            return self.espn
        legacy, rows = Game(fetch=fetch, now=NOW).getNBALiveInfo()
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]['homeTeamName'], 'UTA')
        self.assertEqual(rows[0]['homeTeamScore'], '123')
        self.assertEqual(rows[0]['awayTeamScore'], '132')
        self.assertEqual(team_code('CHA'), 'CHO')
        self.assertEqual(len(legacy), 7)

    def test_stale_and_malformed_scores_rejected(self):
        self.espn['events'][0]['date'] = '2026-05-11T20:00Z'
        with self.assertRaises(ValueError):
            self.game.parse_espn(self.espn)
        with self.assertRaises(KeyError):
            self.game.parse_espn({})

    def test_primary_nba_contract(self):
        data = {'scoreboard': {'gameDate': '2026-10-25', 'games': [{
            'gameId': '1', 'homeTeam': {'teamTricode': 'CHA', 'score': 0},
            'awayTeam': {'teamTricode': 'NYK', 'score': 2}, 'gameStatusText': 'Q1'}]}}
        rows = self.game.parse_nba(data)
        self.assertEqual((rows[0]['homeTeamName'],rows[0]['homeTeamScore']), ('CHO','0'))

    def test_npb_empty_must_be_explicit_and_today(self):
        html = (FIXTURES / 'npb-empty.html').read_text()
        self.assertEqual(self.game.parse_npb(html), ([], []))
        with self.assertRaises(ValueError):
            self.game.parse_npb(html.replace('試合はありません', 'maintenance'))
        with self.assertRaises(ValueError):
            self.game.parse_npb('<html>maintenance</html>')
        with self.assertRaises(ValueError):
            self.game.parse_npb(html.replace('date=2026-10-25','date=2026-10-24'))

    def test_npb_game_scores_and_duplicate(self):
        game = Game(now=NOW-datetime.timedelta(days=1))
        html = (FIXTURES / 'npb-games.html').read_text()
        legacy, rows = game.parse_npb(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['homeTeamScore'],rows[0]['awayTeamScore']), ('3','4'))
        self.assertEqual(rows[0]['homeTeamName'],'DeNA')

    def test_nba_failure_does_not_stop_npb(self):
        game = Mock(now=NOW)
        game.getNBALiveInfo.side_effect = ValueError('bad JSON')
        game.getNPBLiveInfo.return_value = ([], [])
        handler = Mock()
        self.assertEqual(run(game, handler), 1)
        updates, failures, now = handler.publish.call_args[0]
        self.assertEqual(list(updates), ['NPB'])
        self.assertEqual(failures, {'NBA':'ValueError'})

    def test_us_game_day_cutoff(self):
        self.assertEqual(str(self.game.game_day()),'2026-10-25')
        self.assertEqual(str(Game(now=NOW+datetime.timedelta(hours=6)).game_day()),'2026-10-26')

    @patch('Game.time.sleep')
    @patch('Game.requests.get')
    def test_timeout_retries_bounded(self, get, sleep):
        get.side_effect = requests.Timeout()
        with self.assertRaises(requests.Timeout):
            request('https://example.invalid')
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args.kwargs['timeout'], (5,15))


class PublicationTests(unittest.TestCase):
    def test_failed_league_preserved_and_successful_empty_cleared(self):
        nba = dict(homeTeamName='NYK',awayTeamName='PHI',homeTeamScore='1')
        npb = dict(homeTeamName='DeNA',awayTeamName='巨人')
        teams = [dict(league='NPB',searchNM=x) for x in ['DeNA','巨人']]
        self.assertEqual(merge_scores([nba,npb],{'NPB':([],[])},teams),[nba])

    def test_all_failure_performs_no_database_operations(self):
        h = FirestoreHandler.__new__(FirestoreHandler)
        h.db = Mock()
        with self.assertRaises(RuntimeError):
            h.publish({}, {'NBA':'Timeout','NPB':'Timeout'}, NOW)
        h.db.collection.assert_not_called()
        h.db.batch.assert_not_called()

    def test_single_commit_and_failed_commit_does_not_delete_directly(self):
        h = FirestoreHandler.__new__(FirestoreHandler)
        h.db = Mock()
        doc = Mock()
        doc.to_dict.return_value = dict(league='NBA',homeTeamName='NYK',awayTeamName='PHI')
        h.db.collection.return_value.stream.return_value = [doc]
        h.db.batch.return_value.commit.side_effect = RuntimeError('network')
        with self.assertRaises(RuntimeError):
            h.publish({'NBA':([],[])}, {}, NOW)
        h.db.batch.return_value.commit.assert_called_once()
        doc.reference.delete.assert_not_called()


if __name__ == '__main__':
    unittest.main()
