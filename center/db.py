import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def after(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def next_task_number(c, at=None):
    """Called inside BEGIN IMMEDIATE; counter is durable and never reset on deletion."""
    day = (at or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8))).strftime('%Y%m%d')
    while True:
        c.execute("UPDATE counters SET value=value+1 WHERE name='task_number'")
        row = c.execute("SELECT value FROM counters WHERE name='task_number'").fetchone()
        if row is None:
            raise RuntimeError('Task number counter is not initialized')
        number = f'ESOP-{day}-{row[0]:06d}'
        if not c.execute('SELECT 1 FROM playlists WHERE task_number=?', (number,)).fetchone():
            return number


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS assets(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, original_name TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, warnings TEXT NOT NULL DEFAULT '[]', error TEXT, metadata TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS files(
 id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id), path TEXT NOT NULL UNIQUE,
 media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, role TEXT NOT NULL, page_number INTEGER);
CREATE TABLE IF NOT EXISTS conversion_jobs(
 id TEXT PRIMARY KEY, asset_id TEXT NOT NULL UNIQUE REFERENCES assets(id), status TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS playlists(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions(
 id TEXT PRIMARY KEY, playlist_id TEXT NOT NULL REFERENCES playlists(id), name TEXT NOT NULL,
 version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL,
 published_at TEXT, items TEXT NOT NULL DEFAULT '[]', manifest TEXT, UNIQUE(playlist_id,version));
CREATE TRIGGER IF NOT EXISTS published_immutable BEFORE UPDATE ON versions
 WHEN OLD.status='published' BEGIN SELECT RAISE(ABORT, 'published version is immutable'); END;
CREATE TABLE IF NOT EXISTS devices(
 device_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
 actual_version_id TEXT, current_item_id TEXT, playback_state TEXT NOT NULL DEFAULT 'idle',
 pending_version_id TEXT, reported_assignment_revision INTEGER NOT NULL DEFAULT 0,
 report_seq INTEGER NOT NULL DEFAULT 0, last_contact_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS tasks(
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
 playlist_version_id TEXT NOT NULL REFERENCES versions(id), status TEXT NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL, finished_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_task ON tasks((1)) WHERE status='running';
CREATE TABLE IF NOT EXISTS task_targets(
 task_id TEXT NOT NULL REFERENCES tasks(id), device_id TEXT NOT NULL REFERENCES devices(device_id),
 assignment_revision INTEGER NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending', error TEXT,
 PRIMARY KEY(task_id, device_id));
CREATE TABLE IF NOT EXISTS assignments(
 device_id TEXT PRIMARY KEY REFERENCES devices(device_id), assignment_revision INTEGER NOT NULL UNIQUE,
 playlist_version_id TEXT NOT NULL REFERENCES versions(id), task_id TEXT NOT NULL REFERENCES tasks(id),
 assigned_at TEXT NOT NULL, relay_acked_at TEXT);
CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO counters VALUES('assignment_revision',0);
CREATE TABLE IF NOT EXISTS relay_state(id TEXT PRIMARY KEY, last_seen_at TEXT);
CREATE TABLE IF NOT EXISTS events(
 event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(device_id), report_seq INTEGER NOT NULL,
 payload TEXT NOT NULL, received_at TEXT NOT NULL, outcome TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync_receipts(
 device_id TEXT NOT NULL, assignment_revision INTEGER NOT NULL, received_at TEXT NOT NULL,
 PRIMARY KEY(device_id,assignment_revision));
CREATE INDEX IF NOT EXISTS events_device_seq ON events(device_id,report_seq);
"""


class Database:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data_dir / "center.sqlite3"

    def connect(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("PRAGMA synchronous=FULL")
        return c

    def initialize(self):
        for part in ("originals", "derived", "manifests", "tmp"):
            (self.settings.data_dir / part).mkdir(parents=True, exist_ok=True)
        c = self.connect()
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            c.execute('BEGIN IMMEDIATE')
            version = c.execute('SELECT version FROM schema_version').fetchone()[0]
            if version == 1:
                c.execute("ALTER TABLE playlists ADD COLUMN task_number TEXT NOT NULL DEFAULT ''")
                c.execute("ALTER TABLE playlists ADD COLUMN description TEXT NOT NULL DEFAULT ''")
                c.execute('ALTER TABLE playlists ADD COLUMN archived_at TEXT')
                version = 2
            if version == 2:
                previous = [int(match[1]) for row in c.execute('SELECT task_number FROM playlists')
                            if (match := re.fullmatch(r'ESOP-\d{8}-(\d{6,18})', row[0] or ''))]
                c.execute("INSERT INTO counters(name,value) VALUES('task_number',?) ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)", (max(previous, default=0),))
                for row in c.execute("SELECT id FROM playlists WHERE TRIM(task_number)='' ORDER BY rowid").fetchall():
                    c.execute('UPDATE playlists SET task_number=? WHERE id=?', (next_task_number(c), row[0]))
                version = 3
            if version == 3:
                c.execute('CREATE TABLE IF NOT EXISTS version_archives(version_id TEXT PRIMARY KEY REFERENCES versions(id),archived_at TEXT NOT NULL)')
                c.execute('UPDATE schema_version SET version=4')
            elif version != 4:
                raise RuntimeError('Unsupported database schema')
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @contextmanager
    def read(self):
        c = self.connect()
        try:
            c.execute("BEGIN")
            yield c
            c.commit()
        finally:
            c.close()

    @contextmanager
    def write(self):
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()


def expire_tasks(c):
    c.execute("UPDATE tasks SET status='timed_out',finished_at=? WHERE status='running' AND expires_at<=?", (now(), now()))
