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
ANNIV_KEY = "famcal_anniversary"
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
        "reminders": [int(o.get("minutes", 0)) for o in ((g.get("reminders") or {}).get("overrides") or [])],
        "anniversary": priv.get(ANNIV_KEY) == "1",
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
    priv = {}
    if ev.get("tags") is not None:
        priv[TAG_KEY] = ",".join(ev["tags"])
    if "anniversary" in ev:
        priv[ANNIV_KEY] = "1" if ev["anniversary"] else "0"
    if priv:
        body["extendedProperties"] = {"private": priv}
    if "reminders" in ev:   # minutes before start; [] = no reminder (Google: popup overrides)
        body["reminders"] = {"useDefault": False,
                             "overrides": [{"method": "popup", "minutes": int(m)} for m in (ev["reminders"] or [])][:5]}
    return body


class GoogleSource(CalendarSource):
    """Google Calendar REST v3 with an offline refresh token (no Play Services needed)."""
    name = "google"
    API = "https://www.googleapis.com/calendar/v3"

    SCOPE = "https://www.googleapis.com/auth/calendar"

    def __init__(self, token_path, http=None):
        """token_path: a service-account key JSON (type=service_account, preferred) or
        an OAuth token file {client_id, client_secret, refresh_token} (fallback)."""
        self.token_path = token_path
        with open(token_path) as f:
            self.cfg = json.load(f)
        self.mode = "service_account" if self.cfg.get("type") == "service_account" else "oauth"
        self.name = "google-sa" if self.mode == "service_account" else "google"
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

    def _sa_assertion(self):
        """RS256 JWT for the OAuth2 JWT-bearer grant (no user impersonation: the SA acts as
        itself on calendars that were shared with its email)."""
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
        now_ = int(time.time())
        head = {"alg": "RS256", "typ": "JWT"}
        if self.cfg.get("private_key_id"):
            head["kid"] = self.cfg["private_key_id"]
        claims = {"iss": self.cfg["client_email"], "scope": self.SCOPE,
                  "aud": self.cfg.get("token_uri", "https://oauth2.googleapis.com/token"),
                  "iat": now_, "exp": now_ + 3600}
        signing = b64(json.dumps(head).encode()) + b"." + b64(json.dumps(claims).encode())
        key = serialization.load_pem_private_key(self.cfg["private_key"].encode(), password=None)
        return (signing + b"." + b64(key.sign(signing, padding.PKCS1v15(), hashes.SHA256()))).decode()

    def _token(self):
        if self._tok and time.time() < self._exp - 60:
            return self._tok
        if self.mode == "service_account":
            data = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                           "assertion": self._sa_assertion()}).encode()
            st, js = self.http("POST", self.cfg.get("token_uri", "https://oauth2.googleapis.com/token"),
                               {"Content-Type": "application/x-www-form-urlencoded"}, data)
            if st != 200:
                self.last_error = f"service-account token failed ({st}): {js.get('error')}"
                raise RuntimeError(self.last_error)
            self._tok, self._exp = js["access_token"], time.time() + js.get("expires_in", 3600)
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

    def subscribe(self, cal_id):
        """A calendar shared with a service account doesn't show in its calendarList until added."""
        st, js = self.http("POST", self.API + "/users/me/calendarList",
                           {"Authorization": "Bearer " + self._token(), "Content-Type": "application/json"},
                           json.dumps({"id": cal_id}).encode())
        return st in (200, 409), st

    def get_calendar(self, cal_id):
        return self._call("GET", "/calendars/" + urllib.parse.quote(cal_id, safe=""))[1]

    def get_event(self, cal_id, ev_id):
        return self._call("GET", f"/calendars/{urllib.parse.quote(cal_id, safe='')}/events/{ev_id}")[1]

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
        return {"source": self.name, "auth": self.mode, "last_error": self.last_error,
                "account": self.cfg.get("client_email") if self.mode == "service_account" else None}


def _ics_unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_ics(text):
    """Minimal VEVENT reader (unfolds lines, keeps TZID params)."""
    import re
    text = re.sub(r"\r?\n[ \t]", "", text)
    out = []
    for block in re.findall(r"BEGIN:VEVENT\r?\n(.*?)END:VEVENT", text, re.S):
        ev = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            key, _, params = k.partition(";")
            m = re.search(r"TZID=([^;]+)", params)
            if m:
                ev[key + "_TZID"] = m.group(1)
            if "VALUE=DATE" in params and len(v) == 8:
                ev[key + "_DATE"] = True
            ev[key] = _ics_unescape(v)
        out.append(ev)
    return out


def _ics_time(v, tzid=None, is_date=False):
    if is_date or (len(v) == 8 and v.isdigit()):
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}", True
    if v.endswith("Z"):
        v, tzid = v[:-1], "UTC"
    dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if tzid and tzid != str(TZ):
        dt = dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(TZ).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M"), False


class IcsSource(CalendarSource):
    """Read-only public ICS feed (e.g. the PlayMetrics soccer team calendar a service account can't see)."""
    name = "ics"

    def __init__(self, fetch=None):
        self.fetch = fetch or self._fetch
        self.last_error = None

    @staticmethod
    def _fetch(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "famcal/1"}), timeout=60) as r:
            return r.read().decode("utf-8", "replace")

    def list_events(self, url):
        import hashlib
        out = []
        for e in parse_ics(self.fetch(url)):
            if "DTSTART" not in e:
                continue
            st, ad = _ics_time(e["DTSTART"], e.get("DTSTART_TZID"), e.get("DTSTART_DATE"))
            en = st
            if "DTEND" in e:
                en, _ = _ics_time(e["DTEND"], e.get("DTEND_TZID"), e.get("DTEND_DATE"))
                if ad:  # ICS all-day end is exclusive; internal is inclusive
                    en = (date.fromisoformat(en) - timedelta(days=1)).isoformat()
                    en = max(en, st)
            uid = e.get("UID") or (e.get("SUMMARY", "") + st)
            out.append({"id": "ics" + hashlib.sha1(uid.encode()).hexdigest()[:20], "title": e.get("SUMMARY") or "(no title)",
                        "description": e.get("DESCRIPTION", ""), "location": e.get("LOCATION", ""),
                        "all_day": ad, "start": st, "end": en, "rrule": e.get("RRULE"), "exdates": [],
                        "creator": None, "updated": e.get("LAST-MODIFIED") or e.get("DTSTAMP"),
                        "status": "cancelled" if e.get("STATUS") == "CANCELLED" else "confirmed",
                        "master_id": None, "original_start": None, "tags": None})
        return out, True

    def _ro(self, *a):
        raise RuntimeError("this calendar is a read-only ICS feed")
    create = update = delete = _ro

    def set_tags(self, cal_id, ev_id, profiles):
        pass  # tags live in the helper DB for feed events


class RoutingSource(CalendarSource):
    """Sends calendar ids that are URLs to the ICS reader, everything else to the main source."""
    def __init__(self, main, ics=None):
        self.main, self.ics = main, ics or IcsSource()
        self.name = main.name

    def _pick(self, cal_id):
        return self.ics if str(cal_id).startswith(("http://", "https://", "webcal://")) else self.main

    def list_events(self, cal_id):
        return self._pick(cal_id).list_events(str(cal_id).replace("webcal://", "https://", 1))
    def create(self, cal_id, ev): return self._pick(cal_id).create(cal_id, ev)
    def update(self, cal_id, ev_id, ev): return self._pick(cal_id).update(cal_id, ev_id, ev)
    def delete(self, cal_id, ev_id): return self._pick(cal_id).delete(cal_id, ev_id)
    def set_tags(self, cal_id, ev_id, p): return self._pick(cal_id).set_tags(cal_id, ev_id, p)
    def status(self): return self.main.status()
    def __getattr__(self, k):  # list_calendars, subscribe, ... on the Google source
        return getattr(self.main, k)


def google_cred_path(data_dir):
    """Service account key (primary) or OAuth token (fallback), or None."""
    for n in ("google_sa.json", "google_token.json"):
        p = os.path.join(data_dir, n)
        if os.path.exists(p):
            return p
    return None


def make_source(data_dir):
    cred = google_cred_path(data_dir)
    if cred and os.environ.get("FAMCAL_SOURCE", "auto") != "fixture":
        return RoutingSource(GoogleSource(cred))
    return RoutingSource(FixtureSource(os.path.join(data_dir, "fixture_calendar.json")))
