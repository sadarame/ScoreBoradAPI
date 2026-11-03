import datetime
import pathlib
import unittest
from unittest.mock import Mock
from Game import Game
from main import run

FIXTURES = pathlib.Path(__file__).parent / 'fixtures'
NOW = datetime.datetime(2026, 10, 26, 5, tzinfo=datetime.timezone.utc)


def weekly(day='10月26日（月）', year=2026, content='試合はありません'):
    return '''<section><nav><ul class="bb-scheduleNavi__month" id="month_h{year}"><li>{year}年</li></ul></nav>
<table id="wk_sche"><tbody><tr><th>{day}</th><td class="bb-scheduleTable__data--nogame">{content}</td></tr></tbody></table></section>'''.format(year=year, day=day, content=content)


class EmptyNpbTests(unittest.TestCase):
    def setUp(self):
        # Yesterday's completed daily card, same shape observed 2026-10-29.
        self.old = (FIXTURES / 'npb-games.html').read_text()
        self.game = Game(now=NOW)

    def test_last_daily_game_plus_explicit_today_weekly_empty(self):
        self.assertEqual(self.game.parse_npb(self.old + weekly()), ([], []))

    def test_no_extra_request(self):
        fetch = Mock(return_value=self.old + weekly())
        self.assertEqual(Game(now=NOW, fetch=fetch).getNPBLiveInfo(), ([], []))
        fetch.assert_called_once()

    def test_missing_stale_or_contradictory_evidence_is_error(self):
        cases = [
            '', weekly(day='10月27日（火）'), weekly(year=2025),
            weekly().replace('2026年', '2025年'),
            weekly().replace('bb-scheduleNavi__month', 'copyright'),
            weekly(content='maintenance'), weekly(content=''),
            weekly(content='<a href="/npb/game/1">試合はありません</a>'),
            weekly().replace('bb-scheduleTable__data--nogame', 'bb-scheduleTable__data'),
            weekly().replace('</tr>', '<td>試合中</td></tr>'),
            weekly().replace('</tbody>', '<tr><td>試合中</td></tr></tbody>'),
            weekly() + weekly(),
        ]
        for fragment in cases:
            with self.subTest(fragment=fragment):
                with self.assertRaises(ValueError):
                    self.game.parse_npb(self.old + fragment)

    def test_future_daily_card_cannot_fall_back(self):
        future = self.old.replace('10月25日（土）', '10月27日（火）').replace('date=2026-10-24', 'date=2026-10-26')
        # Fixture's weekday is Sunday; replace only the date to be explicit.
        future = future.replace('10月25日（日）', '10月27日（火）')
        with self.assertRaises(ValueError):
            self.game.parse_npb(future + weekly())

    def test_current_daily_game_takes_precedence(self):
        game = Game(now=NOW - datetime.timedelta(days=1))
        _, rows = game.parse_npb(self.old + weekly(day='10月25日（日）'))
        self.assertEqual(len(rows), 1)

    def test_jst_midnight_switch(self):
        before = NOW.replace(hour=14, minute=59)
        after = NOW.replace(hour=15, minute=0)
        self.assertEqual(Game(now=before).parse_npb(self.old + weekly()), ([], []))
        with self.assertRaises(ValueError):
            Game(now=after).parse_npb(self.old + weekly())
        self.assertEqual(Game(now=after).parse_npb(self.old + weekly(day='10月27日（火）')), ([], []))

    def test_nba_success_and_npb_empty_publish_successfully(self):
        game = Mock(now=NOW)
        game.getNBALiveInfo.return_value = ([], [{'league': 'NBA'}])
        game.getNPBLiveInfo.return_value = self.game.parse_npb(self.old + weekly())
        handler = Mock()
        self.assertEqual(run(game, handler), 0)
        updates, failures, _ = handler.publish.call_args[0]
        self.assertEqual(updates['NPB'], ([], []))
        self.assertEqual(failures, {})

    def test_npb_failure_does_not_stop_nba(self):
        game = Mock(now=NOW)
        game.getNBALiveInfo.return_value = ([], [{'league': 'NBA'}])
        game.getNPBLiveInfo.side_effect = ValueError('NPB daily page date mismatch')
        handler = Mock()
        self.assertEqual(run(game, handler), 1)
        updates, failures, _ = handler.publish.call_args[0]
        self.assertEqual(list(updates), ['NBA'])
        self.assertEqual(failures, {'NPB': 'ValueError'})
