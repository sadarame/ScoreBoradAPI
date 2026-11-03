#!/usr/local/bin/python3.7
import fcntl
import logging
import json
import os
from Game import Game
from FirestoreHandler import FirestoreHandler


def run(game=None, handler=None, extra_collectors=(), notifier=None):
    game = game or Game()
    handler = handler or FirestoreHandler()
    updates, failures, delivery = {}, {}, None
    for league, collect in [('NBA', game.getNBALiveInfo), ('NPB', game.getNPBLiveInfo)] + list(extra_collectors):
        try:
            updates[league] = collect()
            logging.info('%s fetched %d matches', league, len(updates[league][1]))
        except Exception as exc:
            failures[league] = type(exc).__name__
            logging.exception('%s failed; retaining previous scores', league)
    # Deliver from freshly collected scores before attempting the remote mirror.
    # A Firestore quota outage must not prevent the next start/final transition.
    if notifier is not None:
        try:
            notifier.observe([row for _, rows in updates.values() for row in rows], game.now)
            delivery = notifier.dispatch(game.now)
            logging.info('Notification delivery: %s', delivery)
            if isinstance(delivery, dict) and delivery.get('failed'):
                failures['notifications'] = 'DeliveryError'
        except Exception:
            logging.exception('Notification pipeline failed')
            failures['notifications'] = 'NotificationError'
    try:
        handler.publish(updates, failures, game.now)
    except Exception:
        logging.exception('Score publication failed; notification polling continues')
        failures['publication'] = 'PublicationError'
    if 'publication' not in failures:
        try:
            handler.publish_team_stats(updates, game.now)
        except Exception:
            logging.exception('Team stats publication failed; live scores preserved')
            failures['stats'] = 'PublicationError'
        if notifier is not None and hasattr(notifier, 'mirror') and 'stats' not in failures:
            try:
                notifier.mirror(handler.db)
            except Exception:
                logging.exception('Notification audit mirror deferred; delivery already processed')
                failures['notificationMirror'] = 'PublicationError'
                if hasattr(handler, 'defer'):
                    handler.defer(game.now)
    logging.info('Collected leagues: %s', ', '.join(updates))
    health_path = os.environ.get('SCOREBOARD_HEALTH_PATH')
    if health_path:
        with open(health_path + '.tmp', 'w') as output:
            json.dump(dict(observedAt=game.now.isoformat(), pushEnabled=notifier is not None,
                           leagues={key: len(value[1]) for key, value in updates.items()},
                           failures=failures, notificationDelivery=delivery if isinstance(delivery, dict) else None), output)
        os.replace(health_path + '.tmp', health_path)
    return 1 if failures else 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    with open(os.path.join(os.path.dirname(__file__), '.scoreboard.lock'), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.info('Previous collector is still running; skipping')
        else:
            from Soccer import Soccer
            from SportsData import SOCCER_LEAGUES
            from Notifications import NotificationDispatcher
            game = Game()
            state_path = os.environ.get('SCOREBOARD_STATE_PATH')
            if state_path:
                from EfficientFirestore import EfficientFirestoreHandler
                from DurableNotifications import DurableNotificationDispatcher
                handler = EfficientFirestoreHandler(state_path)
                notifier = DurableNotificationDispatcher(state_path) if os.environ.get('ENABLE_PUSH') == '1' else None
            else:
                handler = FirestoreHandler()
                notifier = NotificationDispatcher(handler.db) if os.environ.get('ENABLE_PUSH') == '1' else None
            soccer = Soccer(now=game.now)
            leagues = os.environ.get('SOCCER_LEAGUES', ','.join(SOCCER_LEAGUES)).split(',')
            extra = [(key, lambda key=key: soccer.collect(key)) for key in leagues if key in SOCCER_LEAGUES]
            raise SystemExit(run(game, handler, extra, notifier))
