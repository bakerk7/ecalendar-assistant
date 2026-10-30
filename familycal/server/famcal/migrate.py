"""Explicit, one-off copy of eCalendar-only events into the shared Google calendar.

Nothing calls this automatically (not on startup, not on sync, not from /oauth/calendars).

  python -m famcal.migrate cleanup  --stale-ids FILE.json   # drop stale rows of deleted Google copies
  python -m famcal.migrate snapshot --out FILE.json         # read-only: every event in the shared calendar
  python -m famcal.migrate dry-run  --out PLAN.json         # read-only: what would be copied, with dedupe
  python -m famcal.migrate apply    --plan PLAN.json --rollback ROLLBACK.json
  python -m famcal.migrate undo     --rollback ROLLBACK.json  # delete the created copies, restore the rows

Dedupe against Google (normalised title):
  same start -> skip; same day other time -> skip; covered by a Google recurring series -> skip;
  a recurring native whose title already has a Google series -> skip.
"""
import argparse, json, logging, os, sys
from datetime import date, datetime, timedelta

from dateutil.rrule import rrulestr

from .core import Service, _norm_rrule
from .db import DB, now
from .rules import norm_title
from .sources import GoogleSource, RoutingSource, from_google, google_cred_path

log = logging.getLogger("famcal.migrate")
NATIVE = "ecal-native"
DONE = "ecal-native-migrated"


# ------------------------------------------------------------------ DB hygiene
def cleanup_stale(db, stale_google_ids):
    """Forget Google copies that were deleted (rows + tags + meta) and put the eCalendar originals
    back to 'ecal-native' so they show and can be migrated again. Returns (purged, restored)."""
    ids = [i for i in stale_google_ids if i]
    purged = 0
    for i in ids:
        r = db.one("SELECT deleted, origin FROM events WHERE id=?", (i,))
        if r and r["deleted"] and not r["origin"].startswith(NATIVE):
            for t, col in (("events", "id"), ("tags", "event_id"), ("event_meta", "event_id")):
                db.x(f"DELETE FROM {t} WHERE {col}=?", (i,))
            purged += 1
    keep = " AND id NOT IN (SELECT native_id FROM migrations)" if _has_migrations(db) else ""
    restored = db.x(f"UPDATE events SET origin='{NATIVE}', deleted=0 WHERE origin='{DONE}' AND ecal_id IS NOT NULL" + keep).rowcount
    return purged, restored


def _has_migrations(db):
    return bool(db.q("SELECT 1 FROM sqlite_master WHERE type='table' AND name='migrations'"))


def _ensure_table(db):
    db.x("CREATE TABLE IF NOT EXISTS migrations(native_id TEXT PRIMARY KEY, google_id TEXT, at TEXT)")


# ------------------------------------------------------------------ Google read
def snapshot(g, cal_id):
    """Every live event of the calendar (masters + modified instances), raw Google JSON."""
    import urllib.parse
    q = urllib.parse.quote(cal_id, safe="")
    out, page = [], None
    while True:
        p = {"maxResults": 2500, "showDeleted": "false", "singleEvents": "false"}
        if page:
            p["pageToken"] = page
        js = g._call("GET", f"/calendars/{q}/events", p)[1]
        out += [e for e in js.get("items", []) if e.get("status") != "cancelled"]
        page = js.get("nextPageToken")
        if not page:
            return out


# ------------------------------------------------------------------ plan
def _day(s):
    return s[:10]


def _covers(series, day):
    """Does a Google recurring series have an occurrence on `day` (YYYY-MM-DD)?"""
    try:
        st = series["start"]
        dt = datetime.fromisoformat(st if "T" in st else st + "T00:00")
        rule = rrulestr("RRULE:" + _norm_rrule(series["rrule"]), dtstart=dt, forceset=True)
        d0 = datetime.fromisoformat(day + "T00:00")
        return rule.between(d0, d0 + timedelta(days=1) - timedelta(seconds=1), inc=True) != []
    except Exception:
        return False


def plan(db, google_raw):
    gev = [from_google(g) for g in google_raw]
    gev = [e for e in gev if e.get("status") != "cancelled" and e.get("start")]
    by_title = {}
    for e in gev:
        by_title.setdefault(norm_title(e["title"]), []).append(e)
    rows = db.q("""SELECT e.*, t.profiles tprof, m.reminders mrem, m.anniversary manniv FROM events e
                   LEFT JOIN tags t ON t.event_id=e.id LEFT JOIN event_meta m ON m.event_id=e.id
                   WHERE e.origin=? AND e.deleted=0 ORDER BY e.start""", (NATIVE,))
    items = []
    for r in rows:
        nt = norm_title(r["title"])
        cands = by_title.get(nt, [])
        reason, match = None, None
        for c in cands:
            if c["start"] == r["start"] and (c.get("rrule") or None) == (r["rrule"] or None):
                reason, match = "already in Google (same title and start)", c; break
        if not reason:
            for c in cands:
                if c["start"] == r["start"]:
                    reason, match = "already in Google (same title and start, recurrence differs)", c; break
        if not reason and r["rrule"]:
            for c in cands:
                if c.get("rrule"):
                    reason, match = "Google already has a recurring series with this title", c; break
        if not reason:
            for c in cands:
                if _day(c["start"]) == _day(r["start"]):
                    reason, match = "same title on the same day in Google (time differs)", c; break
        if not reason:
            for c in cands:
                if c.get("rrule") and _covers(c, _day(r["start"])):
                    reason, match = "covered by a recurring Google series with this title", c; break
        if not reason and not nt:
            reason = "no title"
        items.append({
            "native_id": r["id"], "ecal_id": r["ecal_id"], "title": r["title"], "start": r["start"], "end": r["end"],
            "all_day": bool(r["all_day"]), "rrule": r["rrule"], "description": r["description"] or "",
            "profiles": json.loads(r["tprof"] or "[]"),
            "reminders": json.loads(r["mrem"]) if r.get("mrem") else None, "anniversary": bool(r.get("manniv")),
            "action": "skip" if reason else "create", "reason": reason or "",
            "match": {"id": match["id"], "title": match["title"], "start": match["start"], "rrule": match.get("rrule")} if match else None,
        })
    return items


# ------------------------------------------------------------------ apply / undo
def apply(svc, items, rollback_path):
    """Create the 'create' items in the shared calendar. The rollback file is rewritten after every
    event, so a crash still leaves an exact list of what was created."""
    _ensure_table(svc.db)
    done = []
    if os.path.exists(rollback_path):
        done = json.load(open(rollback_path)).get("created", [])
    seen = {d["native_id"] for d in done}
    def save():
        tmp = rollback_path + ".tmp"
        json.dump({"at": now(), "calendar": "shared", "created": done}, open(tmp, "w"), indent=1, ensure_ascii=False)
        os.replace(tmp, rollback_path)
    for it in items:
        if it["action"] != "create" or it["native_id"] in seen:
            continue
        r = svc.db.one("SELECT origin, deleted FROM events WHERE id=?", (it["native_id"],))
        if not r or r["origin"] != NATIVE or r["deleted"]:
            continue
        body = {"title": it["title"], "description": it["description"], "start": it["start"], "end": it["end"] or it["start"],
                "all_day": it["all_day"], "rrule": _norm_rrule(it["rrule"]) if it["rrule"] else None,
                "profiles": it["profiles"]}
        if it.get("reminders") is not None:
            body["reminders"] = it["reminders"]
        if it.get("anniversary"):
            body["anniversary"] = True
        ev = svc.create_event(body, "shared", "import")
        svc.db.x(f"UPDATE events SET deleted=1, origin='{DONE}' WHERE id=?", (it["native_id"],))
        svc.db.x("INSERT OR REPLACE INTO migrations(native_id, google_id, at) VALUES(?,?,?)", (it["native_id"], ev["id"], now()))
        done.append({"native_id": it["native_id"], "google_id": ev["id"], "title": it["title"], "start": it["start"]})
        save()
    save()
    return done


def undo(svc, rollback_path):
    data = json.load(open(rollback_path))
    n = 0
    for d in data.get("created", []):
        try:
            svc.delete_event(d["google_id"])
        except KeyError:
            pass
        svc.db.x(f"UPDATE events SET deleted=0, origin='{NATIVE}' WHERE id=?", (d["native_id"],))
        if _has_migrations(svc.db):
            svc.db.x("DELETE FROM migrations WHERE native_id=?", (d["native_id"],))
        n += 1
    return n


# ------------------------------------------------------------------ CLI
def _google(data):
    cred = google_cred_path(data)
    if not cred:
        sys.exit("no Google credentials in " + data)
    return GoogleSource(cred)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["cleanup", "snapshot", "dry-run", "apply", "undo"])
    ap.add_argument("--data", default=os.environ.get("FAMCAL_DATA", "/data"))
    ap.add_argument("--out"); ap.add_argument("--plan"); ap.add_argument("--rollback"); ap.add_argument("--stale-ids")
    a = ap.parse_args(argv)
    db = DB(os.path.join(a.data, "famcal.db"))
    if a.cmd == "cleanup":
        ids = json.load(open(a.stale_ids))
        ids = [i["id"] if isinstance(i, dict) else i for i in ids]
        print("purged %d stale rows, restored %d eCalendar rows" % cleanup_stale(db, ids)); return 0
    g = _google(a.data)
    cal = db.one("SELECT google_id FROM calendars WHERE id='shared'")["google_id"]
    if a.cmd in ("snapshot", "dry-run"):
        raw = snapshot(g, cal)
        if a.cmd == "snapshot":
            json.dump([{"id": e["id"], "updated": e.get("updated"), "summary": e.get("summary")} for e in raw], open(a.out, "w"), indent=1, ensure_ascii=False)
            print("events:", len(raw)); return 0
        items = plan(db, raw)
        json.dump({"google_events": len(raw), "native_rows": len(items),
                   "create": sum(i["action"] == "create" for i in items), "skip": sum(i["action"] == "skip" for i in items),
                   "items": items}, open(a.out, "w"), indent=1, ensure_ascii=False)
        print("google=%d native=%d create=%d skip=%d" % (len(raw), len(items), sum(i["action"] == "create" for i in items), sum(i["action"] == "skip" for i in items)))
        return 0
    svc = Service(db, RoutingSource(g))
    if a.cmd == "apply":
        items = json.load(open(a.plan))["items"]
        print("created", len(apply(svc, items, a.rollback))); return 0
    if a.cmd == "undo":
        print("undone", undo(svc, a.rollback)); return 0


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    sys.exit(main())
