"""Check / finish the Google connection from inside the container (used by install-sa.sh).

python -m famcal.google_setup [--data /data] [--migrate]
  * signs in with data/google_sa.json (service account) or data/google_token.json (OAuth)
  * finds the shared calendar id (env FAMCAL_SHARED_CAL_ID -> helper DB -> imported eCalendar DB)
  * adds it to the account's calendarList, lists visible calendars and access level
  * extendedProperties survival test on a throw-away event (created, edited, checked, deleted)
  * checks the soccer ICS feed (FAMCAL_SOCCER_ICS)
  * --migrate: copy the eCalendar-only events into the shared Google calendar (one time)
Prints no secrets (only the service-account email, calendar names/ids, counts)."""
import argparse, json, os, sqlite3, sys
from collections import Counter

from .db import DB
from .sources import GoogleSource, IcsSource, RoutingSource, google_cred_path, TAG_KEY


def shared_id(db, data):
    v = os.environ.get("FAMCAL_SHARED_CAL_ID", "").strip()
    if v:
        return v, "env"
    r = db.one("SELECT google_id FROM calendars WHERE id='shared'")
    if r and r["google_id"]:
        return r["google_id"], "helper db"
    p = os.path.join(data, "ecal_calendar_db.sqlite")
    if os.path.exists(p):
        c = Counter(x for (x,) in sqlite3.connect(p).execute("SELECT syncCalendarId FROM calendar_event") if x)
        if c:
            return c.most_common(1)[0][0], "eCalendar import"
    return None, None


def ext_props_test(g, cal):
    ok = True
    body = {"summary": "famcal test – safe to delete", "description": "temporary",
            "start": {"date": "2031-01-02"}, "end": {"date": "2031-01-03"},
            "extendedProperties": {"private": {TAG_KEY: "jack,liv"}}}
    from urllib.parse import quote
    q = quote(cal, safe="")
    ev = g._call("POST", f"/calendars/{q}/events", body=body)[1]
    eid = ev["id"]
    try:
        def tag():
            return ((g.get_event(cal, eid).get("extendedProperties") or {}).get("private") or {}).get(TAG_KEY)
        r1 = tag() == "jack,liv"
        g._call("PATCH", f"/calendars/{q}/events/{eid}", body={"summary": "famcal test (edited)", "location": "x"})
        r2 = tag() == "jack,liv"
        full = g.get_event(cal, eid); full["description"] = "edited with a full PUT (what most apps send)"
        g._call("PUT", f"/calendars/{q}/events/{eid}", body=full)
        r3 = tag() == "jack,liv"
        print(f"  extendedProperties after create: {'OK' if r1 else 'MISSING'}")
        print(f"  after PATCH edit (title/location): {'OK' if r2 else 'LOST'}")
        print(f"  after full PUT of the fetched event: {'OK' if r3 else 'LOST'}")
        ok = r1 and r2 and r3
    finally:
        g._call("DELETE", f"/calendars/{q}/events/{eid}")
        print("  test event deleted")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("FAMCAL_DATA", "/data"))
    ap.add_argument("--migrate", action="store_true")
    a = ap.parse_args(argv)
    db = DB(os.path.join(a.data, "famcal.db"))
    cred = google_cred_path(a.data)
    if not cred:
        print("No Google credentials in", a.data); return 2
    g = GoogleSource(cred)
    print(f"Auth mode: {g.mode}" + (f"  account: {g.cfg.get('client_email')}" if g.mode == "service_account" else ""))
    try:
        g._token()
    except Exception as e:
        print("Sign-in FAILED:", e); return 3
    print("Sign-in OK")
    cal, how = shared_id(db, a.data)
    rc = 0
    if not cal:
        print("Shared calendar id unknown: set FAMCAL_SHARED_CAL_ID"); rc = 4
    else:
        print(f"Shared calendar id (from {how}): {cal}")
        ok, st = g.subscribe(cal)
        if not ok:
            print(f"  cannot add it to the calendar list (HTTP {st}). Is it shared with the service account?")
    print("Calendars visible:")
    for c in g.list_calendars():
        print(f"  - {c.get('summary')!s:40} role={c.get('accessRole'):7} id={c.get('id')}")
    if cal:
        try:
            meta = g.get_calendar(cal)
            print(f"Shared calendar reachable: {meta.get('summary')} (tz {meta.get('timeZone')})")
            db.x("UPDATE calendars SET google_id=? WHERE id='shared'", (cal,))
            print("extendedProperties survival test:")
            if not ext_props_test(g, cal):
                rc = 5
        except Exception as e:
            print("Shared calendar NOT usable:", e, "\n  -> share it with the service account email with 'Make changes to events'")
            rc = 4
    ics = os.environ.get("FAMCAL_SOCCER_ICS", "").strip()
    if ics:
        try:
            evs, _ = IcsSource().list_events(ics.replace("webcal://", "https://", 1))
            db.x("UPDATE calendars SET google_id=? WHERE id='soccer'", (ics,))
            print(f"Soccer ICS feed OK: {len(evs)} events (read-only, auto-tagged Jack)")
        except Exception as e:
            print("Soccer ICS feed failed:", type(e).__name__)
    else:
        print("Soccer: FAMCAL_SOCCER_ICS not set (soccer calendar stays on its last data)")
    if a.migrate and rc == 0:
        from .core import Service, migrate_native
        svc = Service(db, RoutingSource(g))
        print("Migrated eCalendar-only events:", migrate_native(svc))
    return rc


if __name__ == "__main__":
    sys.exit(main())
