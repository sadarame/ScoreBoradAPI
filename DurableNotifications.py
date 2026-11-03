"""Notification delivery independent of Firestore availability.

Only the single Cron host may dispatch. Its existing global poll lock serializes
release switches; SQLite commits state + outbox together and claims before FCM.
First observations establish baselines, never replaying matches missed in outages.
Firestore is an audit mirror, never a second dispatcher for this mode.
"""
import datetime
import logging
from firebase_admin import messaging
from LocalState import LocalState, decode
from Notifications import (SUPPORTED_LEAGUES, _aware, game_key, milestone,
                           notification_state, message_payload, delivery_message)

STATE = 'TB_NOTIFICATION_STATE'
OUTBOX = 'TB_NOTIFICATION_OUTBOX'


def significant(value):
    return {k: v for k, v in (value or {}).items()
            if k not in ('observedAt', 'reliableObservedAt', 'expiresAt')}


class DurableNotificationDispatcher:
    def __init__(self, path, send=messaging.send):
        self.store = LocalState(path)
        self.send = send

    def observe(self, rows, now):
        with self.store.connection:
            for row in rows:
                if row.get('league') not in SUPPORTED_LEAGUES:
                    continue
                try:
                    key = game_key(row)
                except (ValueError, KeyError):
                    continue
                old = self.store.get(STATE, key)
                if old and now < old['observedAt']:
                    continue
                current = dict(row)
                for field in ('startsAt', 'gameDate'):
                    if not current.get(field) and old and old.get(field):
                        current[field] = old[field]
                event = milestone(old, current, now)
                state = notification_state(current, old, now)
                dirty = significant(state) != significant(old)
                pending = self.store.connection.execute('SELECT dirty FROM records WHERE namespace=? AND key=?', (STATE, key)).fetchone()
                self.store.put(STATE, key, state, dirty or bool(pending and pending[0]))
                if event and self.store.get(OUTBOX, key + '_' + event) is None:
                    self.store.put(OUTBOX, key + '_' + event,
                        dict(status='pending', createdAt=now, payload=message_payload(current, event),
                             expiresAt=now + datetime.timedelta(days=7)), True)
            # Keep tombstones for the full source/replay window; never prune pending work.
            for namespace in (STATE, OUTBOX):
                for key, value in self.store.entries(namespace):
                    if value.get('expiresAt', now) < now and value.get('status') != 'pending':
                        self.store.connection.execute('DELETE FROM records WHERE namespace=? AND key=?', (namespace, key))

    def dispatch(self, now):
        result = dict(sent=0, failed=0, expired=0)
        for key, candidate in self.store.entries(OUTBOX):
            if candidate.get('status') != 'pending':
                continue
            # The committed claim survives process termination or uncertain FCM responses.
            with self.store.connection:
                self.store.connection.execute('BEGIN IMMEDIATE')
                value = self.store.get(OUTBOX, key)
                if value['status'] != 'pending':
                    continue
                if now - value['createdAt'] > datetime.timedelta(minutes=5):
                    value['status'] = 'expired'
                    self.store.put(OUTBOX, key, value, True)
                    result['expired'] += 1
                    continue
                value.update(status='sending', claimedAt=now)
                self.store.put(OUTBOX, key, value, True)
            try:
                message_id = self.send(delivery_message(value['payload'], key, now))
            except Exception as exc:
                value.update(status='failed', error=type(exc).__name__)
                result['failed'] += 1
                logging.exception('FCM delivery failed: %s', key)
            else:
                value.update(status='sent', messageId=message_id, sentAt=now)
                result['sent'] += 1
            with self.store.connection:
                self.store.put(OUTBOX, key, value, True)
        return result

    def mirror(self, db):
        rows = list(self.store.connection.execute('SELECT namespace,key,value FROM records WHERE dirty=1 AND namespace IN (?,?) LIMIT 400', (STATE, OUTBOX)))
        if not rows:
            return 0
        batch = db.batch()
        for namespace, key, value in rows:
            batch.set(db.collection(namespace).document(key), decode(value))
        batch.commit(retry=None, timeout=8)
        with self.store.connection:
            for namespace, key, value in rows:
                self.store.connection.execute('UPDATE records SET dirty=0 WHERE namespace=? AND key=? AND value=?', (namespace, key, value))
        return len(rows)
