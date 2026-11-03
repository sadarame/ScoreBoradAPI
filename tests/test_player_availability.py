import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from PlayerAvailability import parse_pdf, parse_rows, source_time, collect, save, UTC, ROOT

FIXTURES = Path(__file__).parent / 'fixtures'
PLAYERS_URL = ROOT + 'Injury-Report_2026-01-08_11_00PM.pdf'
PENDING_URL = ROOT + 'Injury-Report_2026-11-03_12_30AM.pdf'
NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)


class AvailabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = parse_pdf((FIXTURES / 'nba-injury-20260108.pdf').read_bytes(), PLAYERS_URL, NOW)
        cls.pending = parse_pdf((FIXTURES / 'nba-injury-20261103.pdf').read_bytes(), PENDING_URL, NOW)

    def test_real_pdf_game_and_player_reasons(self):
        game = next(g for g in self.report['games'] if g['away'] == 'MIL' and g['home'] == 'LAL')
        self.assertEqual(game['gameDateEt'], '2026-01-09')
        player = next(p for p in game['teams']['LAL']['players'] if p['name'] == 'Hachimura, Rui')
        self.assertEqual(player['status'], 'Questionable')
        self.assertEqual(player['reason'], 'Injury/Illness - Right Calf; Strain')

    def test_multiline_and_page_break_reason(self):
        players = [p for g in self.report['games'] for t in g['teams'].values() for p in t['players']]
        haliburton = next(p for p in players if p['name'] == 'Haliburton, Tyrese')
        self.assertEqual(haliburton['reason'], 'Injury/Illness - Right Achilles Tendon; Torn')
        kessler = next(p for p in players if p['name'] == 'Kessler, Walker')
        self.assertEqual(kessler['reason'], 'Injury/Illness - Left Shoulder; Injury recovery')

    def test_pending_is_not_no_injuries_including_page_break(self):
        self.assertEqual(len(self.pending['games']), 14)
        for game in self.pending['games']:
            for team in game['teams'].values():
                self.assertEqual(team['submission'], 'pending')
                self.assertEqual(team['players'], [])
        self.assertEqual(self.pending['reportAt'], '2026-11-03T05:30:00Z')

    def test_wrong_pdf_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            parse_pdf((FIXTURES / 'nba-injury-20260108.pdf').read_bytes(), PENDING_URL, NOW)

    def test_bad_pdf_rejected(self):
        with self.assertRaises(Exception):
            parse_pdf(b'not a pdf', PLAYERS_URL, NOW)

    def test_invalid_source_rejected(self):
        for url in [PLAYERS_URL.replace('https:', 'http:'), PLAYERS_URL+'?x=1',
                    PLAYERS_URL.replace('ak-static.cms.nba.com', 'evil.example')]:
            with self.assertRaises(ValueError):
                source_time(url)

    def test_incomplete_or_unknown_rows_rejected(self):
        row = ['11/03/2026', '07:00 (ET)', 'LAL@SAS', 'Los Angeles Lakers', 'Test, Player', 'Out', 'Rest']
        for rows in [[row], [row, ['', '', '', 'San Antonio Spurs', 'Other, Player', 'Unknown', 'Rest']],
                     [row, ['', '', '', 'Boston Celtics', '', '', 'NOT YET SUBMITTED']]]:
            with self.assertRaises(ValueError):
                parse_rows(rows, source_time(PLAYERS_URL), PLAYERS_URL, NOW)

    def test_bounded_latest_discovery(self):
        seen = []
        raw = (FIXTURES / 'nba-injury-20261103.pdf').read_bytes()
        def fetch(url):
            seen.append(url)
            return raw if url == PENDING_URL else None
        result = collect(NOW, fetch)
        self.assertEqual(result['sourceUrl'], PENDING_URL)
        self.assertEqual(len(seen), 3)
        seen.clear()
        with self.assertRaises(RuntimeError):
            collect(NOW, lambda url: seen.append(url))
        self.assertEqual(len(seen), 9)

    def test_fetch_error_does_not_fall_back_to_old_report(self):
        def fail(url):
            raise ConnectionError('unavailable')
        with self.assertRaises(ConnectionError):
            collect(NOW, fail)

    def test_atomic_save_and_no_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'current.json'
            save(self.pending, path)
            with self.assertRaises(ValueError):
                save(self.report, path)
            self.assertEqual(json.loads(path.read_text()), self.pending)
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
