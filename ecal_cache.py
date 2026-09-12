"""Local sqlite mirror of the eCalendar event list.

Reads (events_on, find_duplicate) hit the local DB instead of the API, so they
are instant and immune to the truncated-response flakiness of /app/event/list.
Writes still go through ecal, then re-sync the affected window.

Typical use:
    import ecal, ecal_cache as cache
    ecal.load_auth_file()
    cache.sync()                                  # back 30d / forward 120d
    cache.events_on("2026-09-21")
    cache.find_duplicate("Dentist", "2026-09-20")
    cache.create_events([{...}, {...}])           # write + re-sync

Keep it fresh with a periodic `cache.sync()` (cron, or before a batch of reads).
Default DB: ~/.ecalendar/cache.db (override with db_path= everywhere).
"""

import json
import os
import sqlite3
from datetime import date, datetime, timedelta

import ecal

DEFAULT_DB = os.path.expanduser("~/.ecalendar/cache.db")
DEFAULT_BACK = 30
DEFAULT_FORWARD = 120
_CHUNK = 30  # days per list call (API needs >= 7)


def _db(db_path=None):
    p = os.path.expanduser(db_path or DEFAULT_DB)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cx = sqlite3.connect(p)
    cx.execute("""CREATE TABLE IF NOT EXISTS events (
        eventId TEXT NOT NULL,
        startDatetime TEXT NOT NULL,
        title TEXT,
        endDatetime TEXT,
        categoryId TEXT,
        allDay INTEGER,
        raw TEXT,
        PRIMARY KEY (eventId, startDatetime))""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_events_start ON events(startDatetime)")
    cx.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return cx


def _row_to_dict(row):
    return json.loads(row["raw"])


def sync(days_back=DEFAULT_BACK, days_forward=DEFAULT_FORWARD, db_path=None):
    """Refresh the mirror for [today-days_back, today+days_forward].

    Chunks the range into _CHUNK-day windows, upserts every fetched row, and
    deletes cached rows in the range that no longer exist server-side.
    Returns (upserted, deleted).
    """
    cx = _db(db_path)
    today = date.today()
    lo = today - timedelta(days=days_back)
    hi = today + timedelta(days=days_forward)
    upserted = 0
    seen = set()
    cur = lo
    while cur <= hi:
        wend = min(cur + timedelta(days=_CHUNK - 1), hi)
        for r in ecal.list_events(cur.isoformat(), wend.isoformat()):
            eid = str(r.get("eventId"))
            sd = r.get("startDatetime") or ""
            seen.add((eid, sd))
            cx.execute(
                "INSERT OR REPLACE INTO events "
                "(eventId, startDatetime, title, endDatetime, categoryId, allDay, raw)"
                " VALUES (?,?,?,?,?,?,?)",
                (eid, sd, r.get("title"), r.get("endDatetime"),
                 str(r.get("userCalendarCategoryId") or r.get("categoryId") or ""),
                 1 if r.get("allDay") else 0, json.dumps(r)))
            upserted += 1
        cur = wend + timedelta(days=1)
    # drop rows in range that the server no longer returns (deleted events)
    placeholders = ",".join("?" for _ in seen)
    deleted = 0
    if seen:
        cur = cx.execute(
            "SELECT eventId, startDatetime FROM events "
            "WHERE date(substr(startDatetime,1,10)) BETWEEN ? AND ?",
            (lo.isoformat(), hi.isoformat()))
        stale = [(e, s) for e, s in cur.fetchall() if (e, s) not in seen]
        for e, s in stale:
            cx.execute("DELETE FROM events WHERE eventId=? AND startDatetime=?", (e, s))
            deleted += 1
    cx.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('last_sync', ?)",
               (datetime.now().isoformat(timespec="seconds"),))
    cx.commit()
    cx.close()
    return upserted, deleted


def last_sync(db_path=None):
    cx = _db(db_path)
    row = cx.execute("SELECT v FROM meta WHERE k='last_sync'").fetchone()
    cx.close()
    return row[0] if row else None


def events_on(day, db_path=None):
    """Cached equivalent of ecal.events_on(day). Returns row dicts."""
    cx = _db(db_path)
    cx.row_factory = sqlite3.Row
    rows = cx.execute(
        "SELECT raw FROM events WHERE substr(startDatetime,1,10) BETWEEN date(?, '-1 day') AND date(?, '+1 day')",
        (day, day)).fetchall()
    cx.close()
    target = date.fromisoformat(day[:10])
    out = []
    for r in rows:
        d = _row_to_dict(r)
        try:
            rd = date.fromisoformat((d.get("startDatetime") or "")[:10])
        except ValueError:
            out.append(d)
            continue
        if abs((rd - target).days) <= 1:
            out.append(d)
    return out


def _words(t):
    import re
    return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2}


def find_duplicate(title, day, db_path=None):
    """Cached equivalent of ecal.find_duplicate: a cached row whose title has
    >=60% word overlap with `title` and whose start is within +/-1 day."""
    want = _words(title)
    if not want:
        return None
    best, best_score = None, 0.0
    for d in events_on(day, db_path):
        have = _words(d.get("title"))
        if not have:
            continue
        score = len(want & have) / max(len(want), len(have))
        if score >= 0.6 and score > best_score:
            best, best_score = d, score
    return best


def create_event(db_path=None, **kw):
    """ecal.create_event + re-sync the surrounding window. kw = create_event kwargs."""
    r = ecal.create_event(**kw)
    start = kw.get("start", "")[:10]
    d = date.fromisoformat(start)
    today = date.today()
    back = max(0, (today - d).days) + 5
    fwd = max(0, (d - today).days) + 5
    sync(days_back=back, days_forward=fwd, db_path=db_path)
    return r


def create_events(specs, db_path=None, max_workers=4):
    """ecal.create_events + one re-sync covering the whole batch's date range."""
    results = ecal.create_events(specs, max_workers=max_workers)
    days = []
    for s in specs:
        try:
            days.append(date.fromisoformat(str(s.get("start", ""))[:10]))
        except ValueError:
            pass
    if days:
        today = date.today()
        back = max(0, (today - min(days)).days) + 5
        fwd = max(0, (max(days) - today).days) + 5
        sync(days_back=back, days_forward=fwd, db_path=db_path)
    return results


if __name__ == "__main__":
    import sys
    ecal.load_auth_file(*(sys.argv[1:] or ()))
    up, gone = sync()
    print(f"synced: {up} upserted, {gone} deleted; last_sync={last_sync()}")
