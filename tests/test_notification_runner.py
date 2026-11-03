import unittest
from notification_runner import poll_window

class RunnerTests(unittest.TestCase):
    def test_five_minute_cron_polls_every_minute(self):
        now, calls = [0], []
        def sleep(seconds): now[0] += seconds
        def poll():
            calls.append(now[0]); now[0] += 8
            raise SystemExit(0)
        poll_window(poll, lambda: now[0], sleep)
        self.assertEqual(calls, [0, 60, 120, 180, 240])
        self.assertLess(now[0], 300)

    def test_slow_poll_skips_missed_slots_without_burst(self):
        now, calls = [0], []
        def sleep(seconds): now[0] += seconds
        def poll(): calls.append(now[0]); now[0] += 130
        poll_window(poll, lambda: now[0], sleep)
        self.assertEqual(calls, [0, 130, 260])

    def test_failed_poll_does_not_stop_next_minute(self):
        now, calls = [0], []
        def sleep(seconds): now[0] += seconds
        def poll():
            calls.append(now[0]); raise RuntimeError('temporary')
        poll_window(poll, lambda: now[0], sleep, duration=120)
        self.assertEqual(calls, [0, 60])
