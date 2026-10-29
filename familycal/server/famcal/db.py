"""SQLite store. One file, WAL, tiny schema. Times are local wall time (TZ env)."""
import json, os, sqlite3, threading, uuid
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL,
  emoji TEXT DEFAULT '', sort INTEGER DEFAULT 0, kind TEXT DEFAULT 'person', ecal_id TEXT);
CREATE TABLE IF NOT EXISTS calendars(id TEXT PRIMARY KEY, name TEXT, google_id TEXT, color TEXT,
  default_profiles TEXT DEFAULT '[]', writable INTEGER DEFAULT 1, ecal_id TEXT);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, cal TEXT NOT NULL, title TEXT NOT NULL,
  description TEXT DEFAULT '', location TEXT DEFAULT '', start TEXT NOT NULL, end TEXT,
  all_day INTEGER DEFAULT 0, rrule TEXT, exdates TEXT DEFAULT '[]', creator TEXT,
  origin TEXT DEFAULT 'google', ecal_id TEXT, updated TEXT, deleted INTEGER DEFAULT 0,
  master_id TEXT, original_start TEXT);
CREATE INDEX IF NOT EXISTS ev_start ON events(start);
CREATE TABLE IF NOT EXISTS tags(event_id TEXT PRIMARY KEY, profiles TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL, rule TEXT, needs_review INTEGER DEFAULT 0, updated TEXT);
CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
  pattern TEXT NOT NULL, profiles TEXT NOT NULL, priority INTEGER DEFAULT 100, note TEXT);
CREATE TABLE IF NOT EXISTS chores(id TEXT PRIMARY KEY, title TEXT NOT NULL, emoji TEXT DEFAULT '',
  profile_id TEXT NOT NULL, stars INTEGER DEFAULT 1, days TEXT DEFAULT '1111111',
  start_date TEXT, end_date TEXT, once_date TEXT, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0,
  ecal_id TEXT);
CREATE TABLE IF NOT EXISTS completions(chore_id TEXT, date TEXT, profile_id TEXT, ts TEXT,
  PRIMARY KEY(chore_id, date));
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL,
  delta INTEGER NOT NULL, reason TEXT, ref TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS rewards(id TEXT PRIMARY KEY, title TEXT NOT NULL, emoji TEXT DEFAULT '🎁',
  cost INTEGER NOT NULL, profile_id TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS lists(id TEXT PRIMARY KEY, name TEXT NOT NULL, emoji TEXT DEFAULT '📝',
  sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS list_items(id TEXT PRIMARY KEY, list_id TEXT NOT NULL, text TEXT NOT NULL,
  done INTEGER DEFAULT 0, sort INTEGER DEFAULT 0, profile_id TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS event_meta(event_id TEXT PRIMARY KEY, reminders TEXT DEFAULT '[]',
  anniversary INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS chore_meta(chore_id TEXT PRIMARY KEY, time TEXT, grp TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

def now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def new_id():
    return uuid.uuid4().hex[:16]

class DB:
    def __init__(self, path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        if path != ":memory:":
            self.c.execute("PRAGMA journal_mode=WAL")
        self.c.executescript(SCHEMA)
        self.c.commit()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args)
            self.c.commit()
            return cur

    def xm(self, sql, rows):
        with self.lock:
            self.c.executemany(sql, rows)
            self.c.commit()

    def meta(self, key, default=None):
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set_meta(self, key, value):
        self.x("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, json.dumps(value)))
