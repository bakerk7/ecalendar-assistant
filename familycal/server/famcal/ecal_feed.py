"""eCalendar-only events, read-only, served next to the Google events.

The stock app's own events (eventSource 0) are never in Google. The helper reads them from the
existing ecal-gateway (GET /events only; it never writes to the gateway or the eCalendar) and keeps
them as origin='ecal-native' rows. Edits made in the new app stay in the helper DB
(origin='ecal-native-edited') and are not overwritten by later refreshes.

Duplicates are hidden (deleted=2, recomputed on every pass), never deleted:
  * a soccer event that the PlayMetrics feed already shows at the same start time
  * the same title already in Google at the same start, the same day, or as an occurrence of a
    Google recurring series with that title (e.g. yearly birthdays already in Google)
"""
import json, logging, os, urllib.parse, urllib.request
from datetime import date, datetime, timedelta

from dateutil.rrule import rrulestr

from .db import now
from .importer import _loc
from .rules import norm_title

log = logging.getLogger("famcal.ecal")
HIDDEN = 2


def fetch(base, key, start, end, timeout=60):
    q = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()})
    req = urllib.request.Request(f"{base.rstrip('/')}/events?{q}", headers={"X-API-Key": key} if key else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["rows"]


def _native_rows(rows):
    """Collapse gateway rows (one per occurrence) to one event per eCalendar eventId."""
    out = {}
    for r in rows:
        if str(r.get("eventType")) != "0" or r.get("eventSource") not in (0, "0") or r.get("isDeleted") in (1, "1"):
            continue
        if r.get("syncThirdEventId") or r.get("syncCalendarId"):
            continue
        eid = str(r["eventId"])
        if eid in out and (out[eid]["startDatetime"] or "") <= (r.get("startDatetime") or ""):
            continue
        out[eid] = r
    return out


def _shape(r):
    allday = bool(int(r.get("isAllDay") or 0))
    if allday:
        start = (r.get("startDatetime") or r.get("startDate") or "")[:10]
        end = (r.get("endDatetime") or start)[:10] or start
    else:
        start = _loc(r["startDatetime"])
        end = _loc(r["endDatetime"]) if r.get("endDatetime") else start
    rr = (r.get("rrule") or None) if int(r.get("isRecurring") or 0) else None
    return {"title": r.get("title") or "(no title)", "description": r.get("description") or "",
            "start": start, "end": end, "all_day": int(allday), "rrule": rr}


def refresh(db, rows, start, end, today=None):
    """Upsert eCalendar-only events from gateway rows covering [start, end]. Returns stats."""
    natives = _native_rows(rows)
    if not natives and rows == []:
        return {"skipped": "empty response"}
    prof_by_ecal = {str(p["ecal_id"]): p["id"] for p in db.q("SELECT id, ecal_id FROM profiles WHERE ecal_id IS NOT NULL")}
    added = updated = removed = 0
    for eid, r in natives.items():
        rid = "ec" + eid
        f = _shape(r)
        cur = db.one("SELECT origin, start, rrule FROM events WHERE id=?", (rid,))
        if cur and cur["origin"] != "ecal-native":
            continue  # edited/hidden locally in the new app: keep the local version
        if cur and cur["rrule"] and f["rrule"]:
            f["start"], f["end"] = cur["start"], None  # gateway returns an occurrence; keep the series start
        if cur:
            sets = {k: v for k, v in f.items() if v is not None}
            db.x(f"UPDATE events SET {','.join(k + '=?' for k in sets)}, deleted=CASE WHEN deleted=1 THEN 0 ELSE deleted END WHERE id=?",
                 (*sets.values(), rid))
            updated += 1
        elif f["rrule"] and any(norm_title(x["title"]) == norm_title(f["title"]) and x["rrule"] == f["rrule"]
                                and _occurs(x["start"], x["rrule"], f["start"][:10])
                                for x in db.q("SELECT title, start, rrule FROM events WHERE origin LIKE 'ecal-native%' AND rrule IS NOT NULL")):
            continue  # a later year of a series we already have (the eCalendar hands out a new id)
        else:
            db.x("""INSERT INTO events(id,cal,title,description,location,start,end,all_day,rrule,exdates,creator,origin,ecal_id,updated,deleted)
                    VALUES(?,?,?,?,'',?,?,?,?,'[]',NULL,'ecal-native',?,?,0)""",
                 (rid, "shared", f["title"], f["description"], f["start"], f["end"], f["all_day"], f["rrule"], eid, now()))
            ids = json.loads(r.get("userCalendarCategoryIds") or "[]") if isinstance(r.get("userCalendarCategoryIds"), str) else (r.get("userCalendarCategoryIds") or [])
            people = [prof_by_ecal[str(i)] for i in ids if str(i) in prof_by_ecal]
            if people:
                db.x("INSERT OR REPLACE INTO tags(event_id,profiles,source,rule,needs_review,updated) VALUES(?,?,'import','eCalendar people',0,?)",
                     (rid, json.dumps(people), now()))
            added += 1
    # gone from the eCalendar: single events inside the window, series not seen at all
    seen = {"ec" + e for e in natives}
    for r in db.q("SELECT id, start, rrule FROM events WHERE origin='ecal-native' AND deleted!=1"):
        if r["id"] in seen:
            continue
        inside = start.isoformat() <= r["start"][:10] <= end.isoformat()
        # Recurring series are never auto-removed: the gateway reports later years of a yearly
        # event under a new eventId, so "not seen" doesn't mean deleted.
        if r["rrule"] is None and inside:
            db.x("UPDATE events SET deleted=1 WHERE id=?", (r["id"],))
            removed += 1
    return {"native": len(natives), "added": added, "updated": updated, "removed": removed}


def _occurs(series_start, rrule, day):
    try:
        dt = datetime.fromisoformat(series_start if "T" in series_start else series_start + "T00:00")
        rule = rrulestr("RRULE:" + rrule.replace("RRULE:", ""), dtstart=dt)
        d0 = datetime.fromisoformat(day + "T00:00")
        return bool(rule.between(d0, d0 + timedelta(hours=23, minutes=59), inc=True))
    except Exception:
        return False


def duplicate_reason(row, others):
    """Why an eCalendar-only row duplicates an event already shown (or None)."""
    nt = norm_title(row["title"])
    day = row["start"][:10]
    for o in others:
        if o["cal"] == "soccer" and o["start"] == row["start"] and "soccer" in nt:
            return "PlayMetrics feed has it: " + o["title"]
    for o in others:
        if o["cal"] == "soccer" or norm_title(o["title"]) != nt:
            continue
        if o["start"] == row["start"] or (not row["rrule"] and not o["rrule"] and o["start"][:10] == day):
            return "already in Google: " + o["title"]
        if o["rrule"] and _occurs(o["start"], o["rrule"], day):
            return "Google series covers it: " + o["title"]
    return None


def dedupe(db):
    """Recompute which eCalendar-only rows are hidden as duplicates. Returns (hidden, reasons)."""
    others = db.q("SELECT id, cal, title, start, rrule FROM events WHERE deleted=0 AND origin NOT LIKE 'ecal-native%'")
    by = {}
    for o in others:
        by.setdefault(o["start"][:10], []).append(o)
        by.setdefault(norm_title(o["title"]), []).append(o)
    hidden, reasons = 0, {}
    mine = db.q("SELECT id, cal, title, start, rrule, deleted FROM events WHERE origin LIKE 'ecal-native%' AND deleted IN (0, 2) ORDER BY start, id")
    first_series = {}
    for r in mine:
        cand = by.get(r["start"][:10], []) + by.get(norm_title(r["title"]), [])
        why = duplicate_reason(r, cand)
        if not why and r["rrule"]:
            # the eCalendar lists later years of a yearly event as a new series; keep the earliest
            k = (norm_title(r["title"]), r["rrule"])
            f = first_series.get(k)
            if f and _occurs(f["start"], f["rrule"], r["start"][:10]):
                why = "same eCalendar series: " + f["id"]
            first_series.setdefault(k, r)
        want = HIDDEN if why else 0
        if want != r["deleted"]:
            db.x("UPDATE events SET deleted=? WHERE id=?", (want, r["id"]))
        if why:
            hidden += 1
            reasons[r["id"]] = why
    return hidden, reasons


class Feed:
    """Background refresh: every FAMCAL_ECAL_SECONDS (default 600) read ECAL_GATEWAY_URL/events."""

    def __init__(self, db, base=None, key=None, back_days=30, ahead_days=400):
        self.db, self.base = db, base or os.environ.get("ECAL_GATEWAY_URL", "")
        self.key = key if key is not None else os.environ.get("ECAL_GATEWAY_KEY", "")
        self.back, self.ahead = back_days, ahead_days
        self.last, self.error, self.stats = None, None, None

    def run_once(self, today=None):
        today = today or date.today()
        s, e = today - timedelta(days=self.back), today + timedelta(days=self.ahead)
        try:
            if self.base:
                self.stats = refresh(self.db, fetch(self.base, self.key, s, e), s, e)
            hidden, _ = dedupe(self.db)
            self.stats = dict(self.stats or {}, hidden_duplicates=hidden)
            self.last, self.error = now(), None
        except Exception as ex:  # keep serving what we have
            self.error = f"{type(ex).__name__}: {ex}"
            log.warning("eCalendar refresh failed: %s", self.error)
        return self.stats
