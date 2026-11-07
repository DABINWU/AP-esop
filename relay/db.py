import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def age(value):
    return (datetime.now(timezone.utc) - datetime.fromisoformat(value.replace('Z', '+00:00'))).total_seconds()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS devices(device_id TEXT PRIMARY KEY, name TEXT NOT NULL, report TEXT,
 report_seq INTEGER NOT NULL DEFAULT 0, last_contact_at TEXT, registered INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS assignments(device_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assignment_history(device_id TEXT NOT NULL, revision INTEGER NOT NULL,
 version_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(device_id,revision));
CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, manifest TEXT NOT NULL, sha256 TEXT NOT NULL,
 files_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'missing', error TEXT,
 retry_at REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, touched_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, descriptor TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'missing', received_bytes INTEGER NOT NULL DEFAULT 0,
 error TEXT, verified_mtime INTEGER);
CREATE TABLE IF NOT EXISTS version_files(version_id TEXT NOT NULL REFERENCES versions(id),
 file_id TEXT NOT NULL REFERENCES files(id), PRIMARY KEY(version_id,file_id));
CREATE TABLE IF NOT EXISTS heads(device_id TEXT PRIMARY KEY, current_version TEXT, previous_version TEXT);
CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
 report_seq INTEGER NOT NULL, payload TEXT NOT NULL, outcome TEXT NOT NULL,
 delivery TEXT NOT NULL DEFAULT 'pending', error TEXT, retry_at REAL NOT NULL DEFAULT 0,
 received_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS outbox_pending ON events(delivery,retry_at,received_at);
'''


class Database:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data_dir/'relay.sqlite3'

    def connect(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA synchronous=FULL')
        c.execute('PRAGMA busy_timeout=15000')
        return c

    def initialize(self):
        for name in ('cache', 'manifests', 'tmp', 'logs'):
            directory = self.settings.data_dir/name
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink() or directory.resolve().parent != self.settings.data_dir:
                raise RuntimeError('Relay storage subdirectories must not be symlinks or junctions')
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript(SCHEMA)
            if c.execute('SELECT version FROM schema_version').fetchone()[0] != 1:
                raise RuntimeError('Unsupported relay database schema')

    @contextmanager
    def read(self):
        c = self.connect()
        try:
            c.execute('BEGIN')
            yield c
            c.commit()
        finally:
            c.close()

    @contextmanager
    def write(self):
        c = self.connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def set_meta(self, key, value):
        with self.write() as c:
            c.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, dumps(value)))

    def meta(self, key, default=None):
        with self.read() as c:
            row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default
