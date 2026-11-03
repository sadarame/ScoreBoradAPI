import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from main import run

class NotificationHealthTests(unittest.TestCase):
    def test_delivery_failure_is_visible_and_scores_are_published(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        game = Mock(now=now)
        game.getNBALiveInfo.return_value = ([], [])
        game.getNPBLiveInfo.return_value = ([], [])
        handler, notifier = Mock(), Mock()
        notifier.dispatch.return_value = {'sent': 0, 'failed': 1}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'health.json')
            with patch.dict(os.environ, {'SCOREBOARD_HEALTH_PATH': path}):
                self.assertEqual(run(game, handler, notifier=notifier), 1)
            with open(path) as source:
                health = json.load(source)
            self.assertTrue(health['pushEnabled'])
            self.assertEqual(health['failures'], {'notifications': 'DeliveryError'})
            handler.publish.assert_called_once()

    def test_disabled_notifications_are_visible(self):
        game = Mock(now=datetime.datetime.now(datetime.timezone.utc))
        game.getNBALiveInfo.return_value = ([], [])
        game.getNPBLiveInfo.return_value = ([], [])
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'health.json')
            with patch.dict(os.environ, {'SCOREBOARD_HEALTH_PATH': path}):
                self.assertEqual(run(game, Mock()), 0)
            with open(path) as source:
                self.assertFalse(json.load(source)['pushEnabled'])
