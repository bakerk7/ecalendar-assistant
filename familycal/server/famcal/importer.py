"""One-time import from the stock app's Room DB (calendar_db) pulled off the tablet.

python -m famcal.importer /path/to/calendar_db [--data /data]
- profiles: names/colors/ecal ids (matched by name to the profiles in config.json)
- calendars: 'Shared Calendar you@example.com' -> shared, the other calendar category -> the feed calendar ('soccer')
- events (eventType 0): Google-synced ones keep their Google event id (syncThirdEventId) so tags
  line up after the real Google switch; eCalendar-only ones are marked origin=ecal-native.
- per-event people tags (userCalendarCategoryIds minus calendar categories); none -> Family
- chores (eventType 2) still active, with stars; star balances; reward list from api_cache
Idempotent: re-running replaces imported rows.
"""
import argparse, json, os, sqlite3, sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .db import DB, now
from .sources import FixtureSource, TZ

UTC = ZoneInfo("UTC")
from . import config
NAME2ID = {p[1].lower(): p[0] for p in config.profiles()}  # stock profile name (lower) -> our id

def _emoji(v):
    """eCalendar stores emoji by Unicode name ("DOG FACE"); turn it back into the character."""
    import unicodedata
    if not v:
        return ""
    if v.isascii():
        try:
            return unicodedata.lookup(v)
        except KeyError:
            return ""
    return v


def _loc(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).astimezone(TZ).strftime("%Y-%m-%dT%H:%M")


def run(src_path, db: DB, fixture: FixtureSource, today=None):
    today = today or date.today()
    s = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    s.row_factory = sqlite3.Row
    cats = [dict(r) for r in s.execute("SELECT * FROM calendar_category")]
    cat2prof, cat2cal = {}, {}
    for c in cats:
        nm = (c["categoryName"] or "").strip()
        pid = NAME2ID.get(nm.lower())
        if pid:
            cat2prof[c["userCalendarCategoryId"]] = pid
            color = c["categoryColor"] or "#888888"
            color = config.importer().get("color_overrides", {}).get(pid, color)  # e.g. keep a profile distinct from the feed colour
            db.x("UPDATE profiles SET color=?, ecal_id=? WHERE id=?", (color, c["userCalendarCategoryId"], pid))
        elif "shared calendar" in nm.lower():
            cat2cal[c["userCalendarCategoryId"]] = "shared"
            db.x("UPDATE calendars SET ecal_id=?, color=? WHERE id='shared'", (c["userCalendarCategoryId"], c["categoryColor"]))
        elif c.get("syncCalenderId"):
            cat2cal[c["userCalendarCategoryId"]] = "soccer"
            db.x("UPDATE calendars SET ecal_id=?, name=?, color=? WHERE id='soccer'",
                 (c["userCalendarCategoryId"], nm, c["categoryColor"]))

    # ---------------- events
    db.x("DELETE FROM tags WHERE event_id IN (SELECT id FROM events WHERE origin IN ('ecal-native','google-import'))")
    db.x("DELETE FROM events WHERE origin IN ('ecal-native','google-import')")
    per_cal = {"shared": [], "soccer": []}
    stats = {"events": 0, "tagged_people": 0, "family": 0, "multi": 0, "native": 0, "synced": 0}
    seen = set()
    for r in s.execute("SELECT * FROM calendar_event WHERE eventType='0'"):
        r = dict(r)
        ids = json.loads(r["userCalendarCategoryIds"] or "[]")
        people = [cat2prof[i] for i in ids if i in cat2prof]
        cal = next((cat2cal[i] for i in ids if i in cat2cal), "shared")
        synced = r["eventSource"] == 2 and r.get("syncThirdEventId")
        eid = r["syncThirdEventId"] if synced else "ec" + str(r["eventId"])
        if eid in seen:
            continue
        seen.add(eid)
        allday = bool(r["isAllDay"])
        if allday:
            start = (r["startDatetime"] or r["startDate"] or "")[:10]
            end = (r["endDatetime"] or r["startDatetime"] or "")[:10] or start
        else:
            if not r["startDatetime"]:
                continue
            start = _loc(r["startDatetime"])
            end = _loc(r["endDatetime"]) if r["endDatetime"] else start
        ex = []
        for x in (r["exDate"] or "").split(","):
            x = x.strip()
            if not x:
                continue
            try:
                if "T" in x:
                    dt = datetime.strptime(x.rstrip("Z"), "%Y%m%dT%H%M%S")
                    if x.endswith("Z"):
                        dt = dt.replace(tzinfo=UTC).astimezone(TZ)
                    ex.append(dt.strftime("%Y-%m-%d") if allday else dt.strftime("%Y-%m-%dT%H:%M"))
                else:
                    ex.append(datetime.strptime(x, "%Y%m%d").date().isoformat())
            except ValueError:
                pass
        ev = {"id": eid, "title": r["title"] or "(no title)", "description": r["description"] or "",
              "location": "", "all_day": allday, "start": start, "end": end,
              "rrule": (r["rrule"] or None) if r["isRecurring"] else None, "exdates": ex,
              "creator": r.get("syncEmail"), "updated": r.get("createTime") or now(), "status": "confirmed",
              "tags": people if people else None}
        per_cal[cal].append(ev)
        origin = "google-import" if synced else "ecal-native"
        db.x("""INSERT OR REPLACE INTO events(id,cal,title,description,location,start,end,all_day,rrule,exdates,
                creator,origin,ecal_id,updated,deleted) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
             (eid, cal, ev["title"], ev["description"], "", start, end, int(allday), ev["rrule"], json.dumps(ex),
              ev["creator"], origin, str(r["eventId"]), ev["updated"]))
        if people:
            src, review = "import", 0
        elif cal == "soccer":
            people, src, review = [p for p in [config.importer().get("feed_calendar_profile")] if p], "rule", 0
        else:
            src, review = "import", 0  # was untagged in eCalendar too -> Family, no review nag for history
        db.x("INSERT OR REPLACE INTO tags(event_id,profiles,source,rule,needs_review,updated) VALUES(?,?,?,?,?,?)",
             (eid, json.dumps(people), src, "imported from eCalendar", review, now()))
        stats["events"] += 1
        stats["native" if not synced else "synced"] += 1
        stats["tagged_people" if people else "family"] += 1
        stats["multi"] += len(people) > 1
    for cal, evs in per_cal.items():
        # the fixture plays "Google": only events that really are in Google carry ext-prop tags
        fixture.seed(cal, evs)

    # ---------------- chores
    db.x("DELETE FROM chores WHERE ecal_id IS NOT NULL")
    nch = 0
    for r in s.execute("SELECT * FROM calendar_event WHERE eventType='2'"):
        r = dict(r)
        ids = json.loads(r["userCalendarCategoryIds"] or "[]")
        people = [cat2prof[i] for i in ids if i in cat2prof] or [config.importer().get("default_chore_profile") or next(iter(NAME2ID.values()), "family")]
        rule = r["rrule"] or ""
        until = None
        if "UNTIL=" in rule:
            until = rule.split("UNTIL=")[1][:8]
            until = f"{until[:4]}-{until[4:6]}-{until[6:8]}"
        sd = (r["startDatetime"] or r["startDate"] or "")[:10]
        if r["isRecurring"]:
            if until and until < today.isoformat():
                continue
            days = "1111111"
            if "FREQ=WEEKLY" in rule and "BYDAY=" in rule:
                by = rule.split("BYDAY=")[1].split(";")[0].split(",")
                days = "".join("1" if d in by else "0" for d in ["MO", "TU", "WE", "TH", "FR", "SA", "SU"])
            once = None
        else:
            if r["completedStatus"] or (sd and sd < today.isoformat()):
                continue
            days, once = "1111111", sd
        for pid in people:
            db.x("""INSERT OR REPLACE INTO chores(id,title,emoji,profile_id,stars,days,start_date,end_date,once_date,
                    active,ecal_id) VALUES(?,?,?,?,?,?,?,?,?,1,?)""",
                 (f"ec{r['eventId']}-{pid}", r["title"], _emoji(r["emoji"]), pid, int(r["starCount"] or 0), days,
                  sd or today.isoformat(), until, once, str(r["eventId"])))
            nch += 1

    # ---------------- stars (balances) + rewards
    db.x("DELETE FROM ledger WHERE ref='import'")
    for c in cats:
        pid = cat2prof.get(c["userCalendarCategoryId"])
        if pid and c["starCount"]:
            db.x("INSERT INTO ledger(profile_id,delta,reason,ref,ts) VALUES(?,?,?,?,?)",
                 (pid, int(c["starCount"]), "balance imported from eCalendar", "import", now()))
    nrw = 0
    row = s.execute("SELECT jsonData FROM api_cache WHERE cacheKey LIKE 'reward_type%'").fetchone()
    if row:
        try:
            for grp in json.loads(row[0]):
                pid = NAME2ID.get((grp.get("categoryName") or "").lower())
                for rw in grp.get("rewardList") or []:
                    t = rw.get("rewardTitle") or rw.get("title") or rw.get("name")
                    cost = rw.get("starCount") or rw.get("star") or rw.get("cost")
                    if t and cost:
                        db.x("INSERT OR REPLACE INTO rewards(id,title,emoji,cost,profile_id) VALUES(?,?,?,?,?)",
                             (f"ec{rw.get('id', t)}", t, _emoji(rw.get("emoji")) or "🎁", int(cost), pid))
                        nrw += 1
        except (ValueError, TypeError):
            pass
    if not db.q("SELECT 1 FROM rewards LIMIT 1"):
        for t, e, c in [("Pick the movie", "🎬", 10), ("Extra 30 min screen time", "📱", 15),
                        ("Ice cream trip", "🍦", 25), ("Stay up late", "🌙", 30), ("Choose dinner", "🍕", 20)]:
            db.x("INSERT INTO rewards(id,title,emoji,cost) VALUES(?,?,?,?)", ("d" + t[:10].replace(" ", ""), t, e, c))
    stats.update(chores=nch, rewards_imported=nrw,
                 stars={p["id"]: p["s"] for p in db.q("SELECT profile_id id, SUM(delta) s FROM ledger GROUP BY profile_id")})
    db.set_meta("import", {"at": now(), "stats": stats})
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("calendar_db")
    ap.add_argument("--data", default=os.environ.get("FAMCAL_DATA", "/data"))
    a = ap.parse_args()
    from .core import Service
    db = DB(os.path.join(a.data, "famcal.db"))
    fx = FixtureSource(os.path.join(a.data, "fixture_calendar.json"))
    Service(db, fx)  # ensures defaults
    print(json.dumps(run(a.calendar_db, db, fx), indent=1))


if __name__ == "__main__":
    main()
