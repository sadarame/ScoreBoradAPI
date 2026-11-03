"""Five one-minute polls per existing five-minute Cron invocation.

A shared lock prevents overlap even across release switches. A failed poll does
not stop later polls; an overrun never produces a burst of catch-up requests.
"""
import fcntl
import logging
import os
import runpy
import time


def poll_window(poll, clock=time.monotonic, sleep=time.sleep, duration=300, interval=60):
    started = clock()
    for index in range(int(duration / interval)):
        delay = started + index * interval - clock()
        if delay > 0:
            sleep(delay)
        if clock() >= started + duration:
            break
        # Skip missed slots; never replay a burst after slow requests.
        if clock() >= started + (index + 1) * interval:
            continue
        try:
            poll()
        except SystemExit as exc:
            if exc.code not in (None, 0):
                logging.warning('Score poll completed with errors: %s', exc.code)
        except Exception:
            logging.exception('Score poll failed; next minute will retry')


def main():
    release = os.path.dirname(os.path.abspath(__file__))
    lock_path = os.environ.get('SCOREBOARD_POLL_LOCK', os.path.join(release, '.poll.lock'))
    with open(lock_path, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.info('Existing minute poller is active; skipping')
            return
        poll_window(lambda: runpy.run_path(os.path.join(release, 'main.py'), run_name='__main__'))


if __name__ == '__main__':
    main()
