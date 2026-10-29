"""Calendar sources behind one interface.

Normalized event dict (all times are local wall time in the service TZ):
  id, title, description, location, all_day(bool),
  start: "YYYY-MM-DD" (all-day) | "YYYY-MM-DDTHH:MM"
  end:   inclusive last date (all-day) | "YYYY-MM-DDTHH:MM" (exclusive instant)
  rrule: "FREQ=...;..." or None, exdates: [local start strings]
  creator: email or None, updated: ISO str, status: "confirmed"|"cancelled"
  master_id / original_start: set for a modified instance of a recurring series
  tags: list of profile ids read from extendedProperties.private, or None if absent
"""
import json, os, threading, time, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TAG_KEY = "famcalProfiles"
TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/Chicago"))


class CalendarSource:
    name = "base"
    def list_events(self, cal_id):  # -> (events, is_full)
        raise NotImplementedError
    def create(self, cal_id, ev): raise NotImplementedError
    def update(self, cal_id, ev_id, ev): raise NotImplementedError
    def delete(self, cal_id, ev_id): raise NotImplementedError
    def set_tags(self, cal_id, ev_id, profiles): raise NotImplementedError
    def status(self): return {"source": self.name}


class FixtureSource(CalendarSource):
    """Stand-in for Google: a JSON file shaped like {cal_id: {ev_id: event}}.
    Seeded from the eCalendar import; lets the whole stack run before OAuth exists."""
    name = "fixture"

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        self.data = {}
        if os.path.exists(path):
            with open(path) as f:
                self.data = json.load(f)

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def seed(self, cal_id, events):
        with self.lock:
            self.data.setdefault(cal_id, {})
            for e in events:
                self.data[cal_id][e["id"]] = e
            self._save()

    def list_events(self, cal_id):
        return [dict(e) for e in self.data.get(cal_id, {}).values()], True

    def create(self, cal_id, ev):
        import uuid
        with self.lock:
            ev = dict(ev, id=ev.get("id") or "fx" + uuid.uuid4().hex[:14],
                      updated=datetime.now().isoformat(), status="confirmed")
            self.data.setdefault(cal_id, {})[ev["id"]] = ev
            self._save()
            return dict(ev)

    def update(self, cal_id, ev_id, ev):
        with self.lock:
            cur = self.data.setdefault(cal_id, {}).get(ev_id)
            if cur is None:
                raise KeyError(ev_id)
            # like Google PATCH: untouched fields (incl. extendedProperties/tags) survive
            cur.update({k: v for k, v in ev.items() if k not in ("id", "tags")})
            cur["updated"] = datetime.now().isoformat()
            self._save()
            return dict(cur)

    def delete(self, cal_id, ev_id):
        with self.lock:
            cur = self.data.get(cal_id, {}).get(ev_id)
            if cur:
                cur["status"] = "cancelled"
                self._save()

    def set_tags(self, cal_id, ev_id, profiles):
        with self.lock:
            cur = self.data.get(cal_id, {}).get(ev_id)
            if cur is not None:
                cur["tags"] = list(profiles)
                self._save()


# ---------------------------------------------------------------- Google
def _g_time(t, all_day_end=False):
    if "date" in t:
        d = date.fromisoformat(t["date"])
        if all_day_end:
            d -= timedelta(days=1)  # Google all-day end is exclusive
        return d.isoformat(), True
    dt = datetime.fromisoformat(t["dateTime"].replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(TZ)
    return dt.strftime("%Y-%m-%dT%H:%M"), False


def _parse_exdates(lines):
    out = []
    for ln in lines:
        if not ln.startswith("EXDATE"):
            continue
        head, _, vals = ln.partition(":")
        for v in vals.split(","):
            v = v.strip()
            try:
                if "T" in v:
                    utc = v.endswith("Z")
                    dt = datetime.strptime(v.rstrip("Z"), "%Y%m%dT%H%M%S")
                    if utc:
                        dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
                    out.append(dt.strftime("%Y-%m-%dT%H:%M"))
                else:
                    out.append(datetime.strptime(v, "%Y%m%d").date().isoformat())
            except ValueError:
                pass
    return out


def from_google(g):
    if g.get("status") == "cancelled" and "start" not in g:
        return {"id": g["id"], "status": "cancelled",
                "master_id": g.get("recurringEventId"),
                "original_start": _g_time(g["originalStartTime"])[0] if g.get("originalStartTime") else None}
    start, all_day = _g_time(g["start"])
    end, _ = _g_time(g.get("end", g["start"]), all_day_end=all_day)
    rec = g.get("recurrence") or []
    rrule = next((r[6:] for r in rec if r.startswith("RRULE:")), None)
    priv = ((g.get("extendedProperties") or {}).get("private") or {})
    tags = priv.get(TAG_KEY)
    return {
        "id": g["id"], "title": g.get("summary") or "(no title)",
        "description": g.get("description") or "", "location": g.get("location") or "",
        "all_day": all_day, "start": start, "end": end, "rrule": rrule,
        "exdates": _parse_exdates(rec), "creator": (g.get("creator") or {}).get("email"),
        "updated": g.get("updated"), "status": g.get("status", "confirmed"),
        "master_id": g.get("recurringEventId"),
        "original_start": _g_time(g["originalStartTime"])[0] if g.get("originalStartTime") else None,
        "tags": [t for t in tags.split(",") if t] if tags is not None else None,
    }


def to_google(ev, tzname=None):
    tzname = tzname or str(TZ)
    body = {}
    for k, gk in (("title", "summary"), ("description", "description"), ("location", "location")):
        if k in ev:
            body[gk] = ev[k]
    if "start" in ev:
        if ev.get("all_day"):
            end = date.fromisoformat((ev.get("end") or ev["start"])[:10]) + timedelta(days=1)
            body["start"] = {"date": ev["start"][:10]}
            body["end"] = {"date": end.isoformat()}
        else:
            body["start"] = {"dateTime": ev["start"] + ":00", "timeZone": tzname}
            body["end"] = {"dateTime": (ev.get("end") or ev["start"]) + ":00", "timeZone": tzname}
    if "rrule" in ev:
        body["recurrence"] = ["RRULE:" + ev["rrule"]] if ev.get("rrule") else []
    if ev.get("tags") is not None:
        body["extendedProperties"] = {"private": {TAG_KEY: ",".join(ev["tags"])}}
    return body


class GoogleSource(CalendarSource):
    """Google Calendar REST v3 with an offline refresh token (no Play Services needed)."""
    name = "google"
    API = "https://www.googleapis.com/calendar/v3"

    def __init__(self, token_path, http=None):
        self.token_path = token_path
        with open(token_path) as f:
            self.cfg = json.load(f)  # client_id, client_secret, refresh_token
        self._tok, self._exp = None, 0
        self.sync_tokens = {}
        self.http = http or self._urlopen
        self.last_error = None

    @staticmethod
    def _urlopen(method, url, headers, body):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {}

    def _token(self):
        if self._tok and time.time() < self._exp - 60:
            return self._tok
        data = urllib.parse.urlencode({
            "client_id": self.cfg["client_id"], "client_secret": self.cfg["client_secret"],
            "refresh_token": self.cfg["refresh_token"], "grant_type": "refresh_token"}).encode()
        st, js = self.http("POST", "https://oauth2.googleapis.com/token",
                           {"Content-Type": "application/x-www-form-urlencoded"}, data)
        if st != 200:
            self.last_error = f"token refresh failed ({st}): {js.get('error')}"
            raise RuntimeError(self.last_error)
        self._tok, self._exp = js["access_token"], time.time() + js.get("expires_in", 3600)
        return self._tok

    def _call(self, method, path, params=None, body=None):
        url = self.API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        hdr = {"Authorization": "Bearer " + self._token(), "Content-Type": "application/json"}
        st, js = self.http(method, url, hdr, json.dumps(body).encode() if body is not None else None)
        if st == 410:
            return st, js
        if st >= 300:
            self.last_error = f"{method} {path} -> {st}"
            raise RuntimeError(self.last_error)
        return st, js

    def list_calendars(self):
        return self._call("GET", "/users/me/calendarList")[1].get("items", [])

    def list_events(self, cal_id):
        q = urllib.parse.quote(cal_id, safe="")
        tok = self.sync_tokens.get(cal_id)
        out, page, full = [], None, tok is None
        while True:
            p = {"maxResults": 2500, "showDeleted": "true", "singleEvents": "false"}
            if tok:
                p["syncToken"] = tok
            if page:
                p["pageToken"] = page
            st, js = self._call("GET", f"/calendars/{q}/events", p)
            if st == 410:  # sync token expired -> full resync
                self.sync_tokens.pop(cal_id, None)
                return self.list_events(cal_id)
            out += [from_google(g) for g in js.get("items", [])]
            page = js.get("nextPageToken")
            if not page:
                if js.get("nextSyncToken"):
                    self.sync_tokens[cal_id] = js["nextSyncToken"]
                break
        return out, full

    def create(self, cal_id, ev):
        q = urllib.parse.quote(cal_id, safe="")
        return from_google(self._call("POST", f"/calendars/{q}/events", body=to_google(ev))[1])

    def update(self, cal_id, ev_id, ev):
        q = urllib.parse.quote(cal_id, safe="")
        ev = {k: v for k, v in ev.items() if k != "tags"}
        return from_google(self._call("PATCH", f"/calendars/{q}/events/{ev_id}", body=to_google(ev))[1])

    def delete(self, cal_id, ev_id):
        q = urllib.parse.quote(cal_id, safe="")
        self._call("DELETE", f"/calendars/{q}/events/{ev_id}")

    def set_tags(self, cal_id, ev_id, profiles):
        q = urllib.parse.quote(cal_id, safe="")
        self._call("PATCH", f"/calendars/{q}/events/{ev_id}",
                   body={"extendedProperties": {"private": {TAG_KEY: ",".join(profiles)}}})

    def status(self):
        return {"source": "google", "last_error": self.last_error}


def make_source(data_dir):
    tok = os.path.join(data_dir, "google_token.json")
    if os.path.exists(tok) and os.environ.get("FAMCAL_SOURCE", "auto") != "fixture":
        return GoogleSource(tok)
    return FixtureSource(os.path.join(data_dir, "fixture_calendar.json"))
