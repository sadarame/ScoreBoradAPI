"""Private durable state for the single Cron host; never place on a public path."""
import datetime
import json
import sqlite3


def encode(value):
    def default(item):
        if isinstance(item, datetime.datetime):
            return {'__scoreboard_datetime__': item.isoformat()}
        raise TypeError(type(item).__name__)
    return json.dumps(value, default=default, ensure_ascii=False, sort_keys=True)


def decode(value):
    def hook(item):
        if set(item) == {'__scoreboard_datetime__'}:
            return datetime.datetime.fromisoformat(item['__scoreboard_datetime__'])
        return item
    return json.loads(value, object_hook=hook)


class LocalState:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS records (namespace TEXT, key TEXT, value TEXT NOT NULL, dirty INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(namespace,key))')
        self.connection.commit()

    def get(self, namespace, key, default=None):
        row = self.connection.execute('SELECT value FROM records WHERE namespace=? AND key=?', (namespace, key)).fetchone()
        return decode(row[0]) if row else default

    def put(self, namespace, key, value, dirty=False):
        self.connection.execute('INSERT OR REPLACE INTO records(namespace,key,value,dirty) VALUES (?,?,?,?)', (namespace, key, encode(value), int(dirty)))

    def entries(self, namespace):
        return [(key, decode(value)) for key, value in self.connection.execute('SELECT key,value FROM records WHERE namespace=?', (namespace,))]
