#!/usr/local/bin/python3.7
import fcntl
import logging
import os
from Game import Game
from FirestoreHandler import FirestoreHandler


def run(game=None, handler=None):
    game = game or Game()
    handler = handler or FirestoreHandler()
    updates, failures = {}, {}
    for league, collect in [('NBA', game.getNBALiveInfo), ('NPB', game.getNPBLiveInfo)]:
        try:
            updates[league] = collect()
            logging.info('%s fetched %d matches', league, len(updates[league][1]))
        except Exception as exc:
            failures[league] = type(exc).__name__
            logging.exception('%s failed; retaining previous scores', league)
    handler.publish(updates, failures, game.now)
    logging.info('Published leagues: %s', ', '.join(updates))
    return 1 if failures else 0


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    with open(os.path.join(os.path.dirname(__file__), '.scoreboard.lock'), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.info('Previous collector is still running; skipping')
        else:
            raise SystemExit(run())
