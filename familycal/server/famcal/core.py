"""Business logic: sync with the calendar source, tagging, recurrence expansion,
chores/stars/rewards, lists."""
import json, os, logging, re, threading, time, urllib.request
from datetime import date, datetime, timedelta
from dateutil.rrule import rrulestr
from zoneinfo import ZoneInfo

from . import rules
from .db import new_id, now
from .sources import TZ

log = logging.getLogger("famcal")
FAMILY = {"id": "family", "name": "Family", "color": "#6B7280", "emoji": "🏠", "kind": "group", "sort": 99}

DEFAULT_PROFILES = [
    ("jack", "Jack", "#4DBAFD", "⚽", 1), ("liv", "Liv", "#E264FF", "🦄", 2),
    ("mom", "Mom", "#17C096", "🌿", 3), ("dad", "Dad", "#EE1B1B", "🔧", 4),
    ("bruce", "Bruce", "#F5A524", "🐶", 5)]


def _norm_rrule(r):
    """eCalendar/Google RRULEs carry UNTIL in UTC (…Z); our DTSTART is naive local."""
    if not r:
        return None
    def fix(m):
        v = m.group(1)
        if v.endswith("Z"):
            dt = datetime.strptime(v[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
            return "UNTIL=" + dt.strftime("%Y%m%dT%H%M%S")
        if len(v) == 8:
            return "UNTIL=" + v + "T235959"
        return "UNTIL=" + v
    return re.sub(r"UNTIL=([0-9TZ]+)", fix, r)


def _pdt(s):
    return datetime.fromisoformat(s if "T" in s else s + "T00:00")


class Service:
    def __init__(self, db, source):
        self.db, self.source = db, source
        self.lock = threading.RLock()
        self._wx = (0, None)
        self.last_sync = None
        self.last_sync_error = None
        self.ensure_defaults()

    # ------------------------------------------------------------ setup
    def ensure_defaults(self):
        if not self.db.one("SELECT COUNT(*) n FROM profiles")["n"]:
            self.db.xm("INSERT INTO profiles(id,name,color,emoji,sort) VALUES(?,?,?,?,?)", DEFAULT_PROFILES)
        if not self.db.one("SELECT COUNT(*) n FROM calendars")["n"]:
            self.db.xm("INSERT INTO calendars(id,name,google_id,color,writable) VALUES(?,?,?,?,?)", [
                ("shared", "Shared Calendar", None, "#2B45DE", 1),
                ("soccer", "Boys U9 Academy 2 White", None, "#4DBAFD", 0)])
        if not self.db.q("SELECT 1 FROM lists LIMIT 1"):
            for i, (n, e) in enumerate([("Groceries", "🛒"), ("To-Do", "✅"), ("Packing", "🎒")]):
                self.db.x("INSERT INTO lists(id,name,emoji,sort) VALUES(?,?,?,?)", (new_id(), n, e, i))
        rules.seed_rules(self.db)
        self.apply_env_calendars()

    def apply_env_calendars(self):
        """FAMCAL_SHARED_CAL_ID (Google calendarId; a service account needs it explicitly) and
        FAMCAL_SOCCER_ICS (public team feed URL, read via IcsSource) override the DB."""
        for cid, env in (("shared", "FAMCAL_SHARED_CAL_ID"), ("soccer", "FAMCAL_SOCCER_ICS")):
            v = os.environ.get(env, "").strip()
            if v:
                self.db.x("UPDATE calendars SET google_id=? WHERE id=?", (v, cid))

    def profiles(self, with_family=True):
        rows = self.db.q("SELECT id,name,color,emoji,sort,kind,ecal_id FROM profiles ORDER BY sort")
        bal = {r["profile_id"]: r["s"] for r in self.db.q("SELECT profile_id, SUM(delta) s FROM ledger GROUP BY profile_id")}
        for r in rows:
            r["stars"] = bal.get(r["id"], 0)
        return rows + ([dict(FAMILY, stars=0)] if with_family else [])

    def valid_ids(self):
        return {r["id"] for r in self.db.q("SELECT id FROM profiles")}

    def calendars(self):
        return self.db.q("SELECT * FROM calendars")

    def _gcal(self, cal):
        if self.source.name == "fixture":
            return cal["id"]
        return cal.get("google_id") or cal["id"]

    # ------------------------------------------------------------ sync
    def sync(self):
        with self.lock:
            try:
                n = 0
                for cal in self.calendars():
                    evs, full = self.source.list_events(self._gcal(cal))
                    seen = set()
                    for ev in evs:
                        seen.add(ev["id"])
                        n += self._upsert(cal, ev)
                    if full:
                        self.db.x(f"UPDATE events SET deleted=1 WHERE cal=? AND deleted=0 AND origin != 'ecal-native' AND id NOT IN ({','.join('?'*len(seen)) or 'NULL'})",
                                  (cal["id"], *seen))
                self.last_sync, self.last_sync_error = now(), None
                return n
            except Exception as e:  # keep serving cached data
                self.last_sync_error = f"{type(e).__name__}: {e}"
                log.warning("sync failed: %s", self.last_sync_error)
                return -1

    def _upsert(self, cal, ev):
        if ev.get("status") == "cancelled":
            if ev.get("master_id") and ev.get("original_start"):
                m = self.db.one("SELECT exdates FROM events WHERE id=?", (ev["master_id"],))
                if m:
                    ex = set(json.loads(m["exdates"] or "[]")); ex.add(ev["original_start"])
                    self.db.x("UPDATE events SET exdates=? WHERE id=?", (json.dumps(sorted(ex)), ev["master_id"]))
            self.db.x("UPDATE events SET deleted=1 WHERE id=?", (ev["id"],))
            return 0
        cur = self.db.one("SELECT updated, deleted FROM events WHERE id=?", (ev["id"],))
        changed = not cur or cur["updated"] != ev.get("updated") or cur["deleted"]
        if changed:
            self.db.x("""INSERT OR REPLACE INTO events(id,cal,title,description,location,start,end,all_day,rrule,
                         exdates,creator,origin,ecal_id,updated,deleted,master_id,original_start)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,COALESCE((SELECT origin FROM events WHERE id=?),'google'),
                         (SELECT ecal_id FROM events WHERE id=?),?,0,?,?)""",
                      (ev["id"], cal["id"], ev.get("title") or "(no title)", ev.get("description", ""),
                       ev.get("location", ""), ev["start"], ev.get("end"), int(bool(ev.get("all_day"))),
                       ev.get("rrule"), json.dumps(ev.get("exdates") or []), ev.get("creator"),
                       ev["id"], ev["id"], ev.get("updated"), ev.get("master_id"), ev.get("original_start")))
        tag = self.db.one("SELECT profiles, source FROM tags WHERE event_id=?", (ev["id"],))
        valid = self.valid_ids()
        if ev.get("tags") is not None:
            want = [p for p in ev["tags"] if p in valid]
            if not tag or json.loads(tag["profiles"]) != want:
                self._store_tags(ev["id"], want, "google", "extendedProperties", 0)
        elif not tag:
            if ev.get("master_id"):  # modified instance inherits the series' tags
                mt = self.db.one("SELECT profiles FROM tags WHERE event_id=?", (ev["master_id"],))
                if mt:
                    self._store_tags(ev["id"], json.loads(mt["profiles"]), "rule", "series", 0)
                    return int(changed)
            prof, src, note, review = rules.decide(self.db, ev, cal["id"], valid)
            self._store_tags(ev["id"], prof, src, note, review)
            if src == "rule" and cal.get("writable"):
                self._push_tags(cal, ev["id"], prof)
        return int(changed)

    def _store_tags(self, eid, profiles, source, rule, review):
        self.db.x("INSERT OR REPLACE INTO tags(event_id,profiles,source,rule,needs_review,updated) VALUES(?,?,?,?,?,?)",
                  (eid, json.dumps(profiles), source, rule, review, now()))

    def _push_tags(self, cal, eid, profiles):
        try:
            self.source.set_tags(self._gcal(cal), eid, profiles)
        except Exception as e:
            log.warning("could not write tags to source for %s: %s", eid, e)

    # ------------------------------------------------------------ events
    def _cal(self, cid):
        c = self.db.one("SELECT * FROM calendars WHERE id=?", (cid,))
        if not c:
            raise KeyError(cid)
        return c

    def set_tags(self, eid, profiles):
        valid = self.valid_ids()
        profiles = [p for p in dict.fromkeys(profiles) if p in valid]
        ev = self.db.one("SELECT cal FROM events WHERE id=?", (eid,))
        if not ev:
            raise KeyError(eid)
        self._store_tags(eid, profiles, "manual", "tagged by hand", 0)
        cal = self._cal(ev["cal"])
        if cal.get("writable"):
            self._push_tags(cal, eid, profiles)
        return self.event(eid)

    def create_event(self, data, cal_id="shared", creator=None):
        cal = self._cal(cal_id)
        prof = data.get("profiles")
        body = {k: data[k] for k in ("title", "description", "location", "start", "end", "all_day", "rrule") if k in data}
        body.setdefault("end", body["start"])
        if prof is not None:
            body["tags"] = [p for p in prof if p in self.valid_ids()]
        with self.lock:
            ev = self.source.create(self._gcal(cal), body)
            ev.setdefault("creator", creator)
            self._upsert(cal, ev)
            if prof is not None:
                self._store_tags(ev["id"], body["tags"], "manual", "set on create", 0)
        return self.event(ev["id"])

    def update_event(self, eid, data):
        e = self.db.one("SELECT * FROM events WHERE id=?", (eid,))
        if not e:
            raise KeyError(eid)
        cal = self._cal(e["cal"])
        body = {k: data[k] for k in ("title", "description", "location", "start", "end", "all_day", "rrule") if k in data}
        if "start" in body and "all_day" not in body:
            body["all_day"] = bool(e["all_day"])
        if "start" in body and "end" not in body:
            body["end"] = body["start"]
        with self.lock:
            if body:
                ev = self.source.update(self._gcal(cal), eid, body)
                self._upsert(cal, ev)
            if "profiles" in data:
                self.set_tags(eid, data["profiles"])
        return self.event(eid)

    def delete_event(self, eid):
        e = self.db.one("SELECT cal FROM events WHERE id=?", (eid,))
        if not e:
            raise KeyError(eid)
        self.source.delete(self._gcal(self._cal(e["cal"])), eid)
        self.db.x("UPDATE events SET deleted=1 WHERE id=?", (eid,))

    def event(self, eid):
        r = self.db.one("""SELECT e.*, t.profiles tprof, t.source tsrc, t.rule trule, t.needs_review
                           FROM events e LEFT JOIN tags t ON t.event_id=e.id WHERE e.id=?""", (eid,))
        return self._shape(r) if r else None

    def _shape(self, r, inst_start=None, inst_end=None):
        prof = json.loads(r.get("tprof") or "[]")
        return {"id": r["id"], "cal": r["cal"], "title": r["title"], "description": r["description"],
                "location": r["location"], "all_day": bool(r["all_day"]),
                "start": inst_start or r["start"], "end": inst_end or r["end"],
                "series_start": r["start"], "rrule": r["rrule"], "recurring": bool(r["rrule"]),
                "profiles": prof or ["family"], "tag_source": r.get("tsrc"), "tag_rule": r.get("trule"),
                "needs_tag": bool(r.get("needs_review")), "origin": r.get("origin")}

    def instances(self, start, end):
        """Expanded occurrences overlapping [start, end] (dates, inclusive)."""
        s = datetime.combine(start, datetime.min.time())
        e = datetime.combine(end, datetime.max.time())
        rows = self.db.q("""SELECT e.*, t.profiles tprof, t.source tsrc, t.rule trule, t.needs_review
                            FROM events e LEFT JOIN tags t ON t.event_id=e.id
                            WHERE e.deleted=0 AND (e.rrule IS NOT NULL OR (e.start <= ? AND COALESCE(e.end,e.start) >= ?))""",
                         (e.strftime("%Y-%m-%dT%H:%M"), s.strftime("%Y-%m-%d")))
        overrides = {(r["master_id"], r["original_start"]) for r in rows if r.get("master_id")}
        out = []
        for r in rows:
            st = _pdt(r["start"])
            en = _pdt(r["end"] or r["start"])
            if r["all_day"]:
                en = en + timedelta(days=1) - timedelta(seconds=1)
            dur = en - st
            if not r["rrule"]:
                if st <= e and en >= s:
                    out.append(self._shape(r))
                continue
            try:
                rr = rrulestr(_norm_rrule(r["rrule"]), dtstart=st)
            except (ValueError, TypeError):
                continue
            ex = set(json.loads(r["exdates"] or "[]"))
            for occ in rr.between(s - dur, e, inc=True):
                key = occ.strftime("%Y-%m-%d") if r["all_day"] else occ.strftime("%Y-%m-%dT%H:%M")
                if key in ex or occ.strftime("%Y-%m-%d") in ex or (r["id"], key) in overrides:
                    continue
                oe = occ + dur
                if r["all_day"]:
                    out.append(self._shape(r, key, (oe.date()).isoformat()))
                else:
                    out.append(self._shape(r, key, oe.strftime("%Y-%m-%dT%H:%M")))
        out.sort(key=lambda x: (x["start"][:10], not x["all_day"], x["start"]))
        return out

    def to_tag(self, limit=100):
        today = date.today().isoformat()
        rows = self.db.q("""SELECT e.*, t.profiles tprof, t.source tsrc, t.rule trule, t.needs_review
                            FROM events e JOIN tags t ON t.event_id=e.id
                            WHERE e.deleted=0 AND t.needs_review=1 AND (e.rrule IS NOT NULL OR COALESCE(e.end,e.start) >= ?)
                            ORDER BY e.start LIMIT ?""", (today, limit))
        return [self._shape(r) for r in rows]

    # ------------------------------------------------------------ chores & stars
    def chores_for(self, d):
        dow = d.weekday()
        ds = d.isoformat()
        rows = self.db.q("SELECT * FROM chores WHERE active=1 ORDER BY profile_id, sort, title")
        done = {r["chore_id"]: r for r in self.db.q("SELECT * FROM completions WHERE date=?", (ds,))}
        out = []
        for c in rows:
            if c["once_date"]:
                due = c["once_date"] == ds
            else:
                due = (c["days"] or "1111111")[dow] == "1" and (not c["start_date"] or c["start_date"] <= ds) \
                      and (not c["end_date"] or c["end_date"] >= ds)
            if due:
                out.append(dict(c, done=c["id"] in done, date=ds))
        return out

    def toggle_chore(self, cid, d, done=None):
        c = self.db.one("SELECT * FROM chores WHERE id=?", (cid,))
        if not c:
            raise KeyError(cid)
        ds = d.isoformat()
        with self.lock:
            cur = self.db.one("SELECT 1 FROM completions WHERE chore_id=? AND date=?", (cid, ds))
            want = (not cur) if done is None else bool(done)
            if want and not cur:
                self.db.x("INSERT INTO completions VALUES(?,?,?,?)", (cid, ds, c["profile_id"], now()))
                if c["stars"]:
                    self.db.x("INSERT INTO ledger(profile_id,delta,reason,ref,ts) VALUES(?,?,?,?,?)",
                              (c["profile_id"], c["stars"], "chore: " + c["title"], f"chore:{cid}:{ds}", now()))
            elif not want and cur:
                self.db.x("DELETE FROM completions WHERE chore_id=? AND date=?", (cid, ds))
                self.db.x("DELETE FROM ledger WHERE ref=?", (f"chore:{cid}:{ds}",))
        return {"done": want, "stars": self.balance(c["profile_id"])}

    def balance(self, pid):
        return self.db.one("SELECT COALESCE(SUM(delta),0) s FROM ledger WHERE profile_id=?", (pid,))["s"]

    def add_chore(self, d):
        cid = new_id()
        self.db.x("""INSERT INTO chores(id,title,emoji,profile_id,stars,days,start_date,end_date,once_date,active)
                     VALUES(?,?,?,?,?,?,?,?,?,1)""",
                  (cid, d["title"], d.get("emoji", ""), d["profile_id"], int(d.get("stars", 1)),
                   d.get("days", "1111111"), d.get("start_date") or date.today().isoformat(),
                   d.get("end_date"), d.get("once_date")))
        return self.db.one("SELECT * FROM chores WHERE id=?", (cid,))

    def redeem(self, rid, pid):
        r = self.db.one("SELECT * FROM rewards WHERE id=?", (rid,))
        if not r:
            raise KeyError(rid)
        with self.lock:
            bal = self.balance(pid)
            if bal < r["cost"]:
                raise ValueError(f"not enough stars ({bal} < {r['cost']})")
            self.db.x("INSERT INTO ledger(profile_id,delta,reason,ref,ts) VALUES(?,?,?,?,?)",
                      (pid, -r["cost"], "reward: " + r["title"], f"reward:{rid}", now()))
        return {"stars": self.balance(pid)}

    # ------------------------------------------------------------ weather
    def weather(self):
        t, data = self._wx
        if data and time.time() - t < 900:
            return data
        url = ("https://api.open-meteo.com/v1/forecast?latitude=40.7128&longitude=-74.006"
               "&current=temperature_2m,weather_code,is_day,apparent_temperature,wind_speed_10m"
               "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
               "&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=America%2FChicago&forecast_days=7")
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                js = json.loads(r.read())
            data = {"place": "your town", "current": js.get("current"), "daily": js.get("daily"), "fetched": now()}
            self._wx = (time.time(), data)
        except Exception as e:
            log.warning("weather failed: %s", e)
            if data:
                return dict(data, stale=True)
            return {"error": "weather unavailable"}
        return data


def migrate_native(svc, since_days=60):
    """Copy eCalendar-only events (never in Google) into the shared Google calendar, keeping tags.
    Called once when switching from the fixture to Google. Returns count."""
    cutoff = (date.today() - timedelta(days=since_days)).isoformat()
    rows = svc.db.q("""SELECT e.*, t.profiles tprof FROM events e LEFT JOIN tags t ON t.event_id=e.id
                       WHERE e.origin='ecal-native' AND e.deleted=0 AND (e.rrule IS NOT NULL OR COALESCE(e.end,e.start) >= ?)""",
                    (cutoff,))
    n = 0
    for r in rows:
        body = {"title": r["title"], "description": r["description"], "start": r["start"], "end": r["end"],
                "all_day": bool(r["all_day"]), "rrule": _norm_rrule(r["rrule"]) if r["rrule"] else None,
                "profiles": json.loads(r["tprof"] or "[]")}
        try:
            svc.create_event(body, "shared", "import")
            svc.db.x("UPDATE events SET deleted=1, origin='ecal-native-migrated' WHERE id=?", (r["id"],))
            n += 1
        except Exception as e:
            log.warning("migrate %s failed: %s", r["id"], e)
    return n
