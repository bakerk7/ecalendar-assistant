"""Read-only family wall dashboard served by the gateway at /dashboard.

No API key is needed (or exposed): the browser only talks to these read-only
routes, which call the eCalendar session server-side. LAN/tailnet only.

Env:
  DASHBOARD_ENABLED        default 1
  DASHBOARD_CAMERA_URL     go2rtc embed URL (e.g. http://host:1984/stream.html?src=door&mode=webrtc,mse)
                           '{host}' is replaced client-side with the page's hostname
  DASHBOARD_CAMERA_NAME    default "Camera"
  DASHBOARD_DOORBELL_SECRET  shared secret for POST /dashboard/api/doorbell/ring
                           (header X-Doorbell-Secret); ring endpoint is disabled if unset
  DASHBOARD_LAT/LON/PLACE  weather location (default Maple Grove, MN); NWS api.weather.gov
  DASHBOARD_WEATHER_UA     User-Agent sent to NWS (they require a contact)
  DASHBOARD_NIGHT_START/END  night-dim window, local HH:MM (default 21:00 / 06:00)
  HA_URL / HA_TOKEN        Home Assistant base URL + long-lived token (read-only GET /api/states)
  DASHBOARD_HA_ENTITIES    comma list of entity_id[=Label] shown as header chips (default person.kyle_baker)
  DASHBOARD_DOORBELL_ENTITY HA event entity polled for rings (default event.doorbell_doorbell)
  DASHBOARD_CHORE_PEOPLE   comma list of profiles always shown in chores (default Jack,Liv)
  DASHBOARD_ADD_PEOPLE     profiles offered in the '+ Add' form (default Jack,Liv,Mom,Dad,Bruce)

Writes: POST /dashboard/api/events/create and /dashboard/api/chores/create create
events/chores with the current eCalendar token only (run_token: never re-logs in).
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import re
import time
import urllib.request
from typing import Optional
from datetime import datetime, timezone
from datetime import timedelta

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse

import ecal

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Display rules: category name -> (label shown, category whose color to use)
ALIASES = {"Boys U9 Academy 2 White": "Jack"}
ORDER = ["Jack", "Liv", "Mom", "Dad", "Bruce", "Family"]


def display_name(name):
    name = name or ""
    if name in ALIASES:
        return ALIASES[name]
    if name.startswith("Shared Calendar"):
        return "Family"
    return name


def people(categories):
    """[{name,color,initial}] in display order, deduped (first color wins)."""
    seen = {}
    for c in categories:
        n = display_name(c.get("categoryName"))
        if n and n not in seen:
            seen[n] = c.get("categoryColor") or "#64748b"
    names = sorted(seen, key=lambda n: (ORDER.index(n) if n in ORDER else 99, n))
    return [{"name": n, "color": seen[n], "initial": n[:1].upper()} for n in names]


def normalize(row):
    """eCalendar list row -> {id,title,start,end,allDay,people}. Local (America/Chicago) times."""
    all_day = ecal._is_all_day(row)
    try:
        s = ecal._parse_clock(row.get("startDatetime") or "")
        e = ecal._parse_clock(row.get("endDatetime") or row.get("startDatetime") or "")
    except ValueError:
        return None
    if all_day:  # all-day rows carry a local calendar date, not a UTC instant
        start, end = s.date().isoformat(), e.date().isoformat()
    else:        # timed rows are UTC
        start = ecal._utc_to_local(s).isoformat(timespec="minutes")
        end = ecal._utc_to_local(e).isoformat(timespec="minutes")
    return {"id": str(row.get("eventId") or row.get("id") or ""),
            "title": row.get("title") or "(untitled)", "start": start, "end": end,
            "allDay": bool(all_day),
            "people": [p["name"] for p in people(row.get("userCalendarCategorys") or [])]}


ADD_DEFAULT = "Jack,Liv,Mom,Dad,Bruce"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def add_people(categories):
    """[{name,id,color}] for the '+ Add' picker, matched to profiles by name.
    An exact categoryName match wins over an alias (e.g. the soccer-team calendar)."""
    want = [n.strip() for n in os.environ.get("DASHBOARD_ADD_PEOPLE", ADD_DEFAULT).split(",") if n.strip()]
    out = []
    for n in want:
        hit = next((c for c in categories if (c.get("categoryName") or "").lower() == n.lower()), None)
        if hit is None:
            hit = next((c for c in categories if display_name(c.get("categoryName")) == n), None)
        if hit is not None:
            out.append({"name": n, "id": str(hit.get("userCalendarCategoryId")),
                        "color": hit.get("categoryColor") or "#64748b"})
    return out


def _clean_title(body):
    t = str((body or {}).get("title") or "").strip()
    if not t:
        raise HTTPException(400, "title required")
    if len(t) > 120:
        raise HTTPException(400, "title too long (max 120)")
    return t


def _clean_day(v):
    v = str(v or "")
    if not _DAY.match(v):
        raise HTTPException(400, "date must be YYYY-MM-DD")
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "invalid date")
    return v


def _clean_hm(v, name):
    v = str(v or "")
    if not _HM.match(v):
        raise HTTPException(400, f"{name} must be HH:MM (24h)")
    return v


def _pick(ids, allowed):
    ids = [str(i) for i in (ids or []) if str(i)]
    by_id = {p["id"]: p for p in allowed}
    if not ids or any(i not in by_id for i in ids):
        raise HTTPException(400, "pick at least one profile from the list")
    out = []
    for i in ids:
        if i not in out:
            out.append(i)
    return out


def event_spec(body, allowed):
    """Validated dashboard form -> ecal.create_event kwargs (local wall times)."""
    title = _clean_title(body)
    day = _clean_day(body.get("date"))
    cats = _pick(body.get("profiles"), allowed)
    if body.get("allDay"):
        return {"title": title, "start": day, "all_day": True, "category": cats}
    s = _clean_hm(body.get("start"), "start")
    if body.get("end"):
        e = _clean_hm(body.get("end"), "end")
    else:
        h, m = map(int, s.split(":"))
        e = f"{h + 1:02d}:{m:02d}" if h < 23 else "23:59"
    if e <= s:
        raise HTTPException(400, "end must be after start")
    return {"title": title, "start": f"{day} {s}:00", "end": f"{day} {e}:00",
            "all_day": False, "category": cats, "reminders_min": (0,)}


def chore_spec(body, allowed):
    title = _clean_title(body)
    day = _clean_day(body.get("date"))
    cats = _pick(body.get("profiles"), allowed)
    try:
        stars = int(body.get("stars") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "stars must be a number")
    if not 0 <= stars <= 20:
        raise HTTPException(400, "stars must be 0-20")
    at = _clean_hm(body.get("time"), "time") if body.get("time") else None
    return {"title": title, "due": day, "categories": cats, "stars": stars, "time": at}


def chore_body(spec, cat):
    """One chore for one profile. Same body as ecal.create_task / the app's one-off
    chore (all-day, due-date marker 23:59:59 local, emoji null). An optional time
    becomes a popup reminder at that local time on the due day (minutes after
    midnight -- the convention all-day events use for their 9:00 AM = 540)."""
    d = spec["due"]
    body = {
        "deviceId": str(ecal.DEVICE), "title": spec["title"], "description": "",
        "eventType": "2", "taskType": 0,
        "isAllDay": 1, "startDatetime": f"{d} 23:59:59",
        "userCalendarCategoryIds": [cat], "zone": ecal.tz_offset(d),
        "emoji": None, "starCount": str(spec["stars"]), "priority": "0",
        "timerDurationSeconds": None,
        "taskTimeoutPenalty": 0, "taskTimeoutPenaltyStarPercent": "0.00",
        "taskTimeoutPenaltyStarCount": 0,
        "isRecurring": 0, "eventRecurrenceRule": None,
        "timeReminders": [], "reminderPipelineVersion": 1,
    }
    if spec.get("time"):
        h, m = map(int, spec["time"].split(":"))
        body["timeReminders"] = [{"method": "popup", "timeUnit": "minute", "value": h * 60 + m}]
    return body


def make_router(run, cache, parse_day, run_token=None) -> APIRouter:
    r = APIRouter(include_in_schema=False)
    run_token = run_token or run

    @r.get("/dashboard")
    def index_redirect():
        return RedirectResponse("/dashboard/")

    @r.get("/dashboard/")
    def index():
        return FileResponse(os.path.join(STATIC, "dashboard.html"),
                            headers={"Cache-Control": "no-cache"})

    @r.get("/dashboard/app.apk")
    def kiosk_apk():
        path = os.path.join(STATIC, "app.apk")
        if not os.path.isfile(path):
            raise HTTPException(404, "app.apk not found")
        return FileResponse(path, media_type="application/vnd.android.package-archive",
                            filename="FamilyDashboard.apk", headers={"Cache-Control": "no-cache"})

    @r.get("/dashboard/api/config")
    def config():
        cats = cache.get_or_set("categories", lambda: run(lambda t: ecal.list_categories(t)))
        return {
            "timezone": ecal.TIMEZONE,
            "people": people(cats),
            "addPeople": add_people(cats),
            "camera": {"url": os.environ.get("DASHBOARD_CAMERA_URL", ""),
                       "name": os.environ.get("DASHBOARD_CAMERA_NAME", "Camera")},
            "weather": {"lat": float(os.environ.get("DASHBOARD_LAT", "45.0725")),
                        "lon": float(os.environ.get("DASHBOARD_LON", "-93.4558")),
                        "place": os.environ.get("DASHBOARD_PLACE", "Maple Grove, MN")},
            "night": {"start": os.environ.get("DASHBOARD_NIGHT_START", "21:00"),
                      "end": os.environ.get("DASHBOARD_NIGHT_END", "06:00")},
        }

    # ------------------------------------------------------------ chores / stars
    def _today():
        return ecal._utc_to_local(datetime.utcnow()).date().isoformat()

    def _chore_day(row):
        return (row.get("routineInstanceDate") or row.get("startDatetime") or "")[:10]

    def _chores(day, fresh=False):
        key = f"chores:{day}"
        if fresh:
            cache.clear()

        def fetch():
            cats = run(lambda t: ecal.list_categories(t))
            kids = {c["userCalendarCategoryId"]: c for c in cats if c.get("categoryType") == 1}
            rows = run(lambda t: ecal.list_tasks(day, list(kids), t)) if kids else []
            stats = run(lambda t: ecal.category_stats(day, t))
            return kids, rows, stats
        return cache.get_or_set(key, fetch)

    def chore_payload(day):
        kids, rows, stats = _chores(day)
        stars = {c.get("userCalendarCategoryId"): c.get("starCount") or 0
                 for c in (stats or {}).get("categories") or []}
        always = [n.strip() for n in os.environ.get("DASHBOARD_CHORE_PEOPLE", "Jack,Liv").split(",") if n.strip()]
        out = {}
        for cid, c in kids.items():
            out[cid] = {"id": cid, "name": c.get("categoryName"), "color": c.get("categoryColor") or "#64748b",
                        "stars": stars.get(cid, 0), "chores": []}
        for r in rows:
            if _chore_day(r) != day:
                continue
            for c in r.get("userCalendarCategorys") or []:
                k = out.get(c.get("userCalendarCategoryId"))
                if k is None:
                    continue
                k["chores"].append({"id": str(r.get("eventId")), "title": r.get("title") or "",
                                    "emoji": r.get("emoji"), "stars": r.get("starCount") or 0,
                                    "done": bool(r.get("completedStatus")),
                                    "period": r.get("routinePeriod"),
                                    "tappable": not r.get("isGenerate")})
        kids_out = [k for k in out.values() if k["chores"] or k["name"] in always]
        kids_out.sort(key=lambda k: (always.index(k["name"]) if k["name"] in always else 99, k["name"]))
        for k in kids_out:
            k["chores"].sort(key=lambda x: (x["done"], x["period"] or 0, x["title"]))
        return {"day": day, "kids": kids_out}

    @r.get("/dashboard/api/chores")
    def chores():
        return chore_payload(_today())

    @r.post("/dashboard/api/chores/toggle")
    def chore_toggle(body: dict = Body(...)):
        eid = str(body.get("eventId") or "")
        day = _today()
        kids, rows, _ = _chores(day, fresh=True)
        row = next((x for x in rows if str(x.get("eventId")) == eid and _chore_day(x) == day), None)
        if row is None:
            raise HTTPException(404, "not one of today's chores")
        if row.get("isGenerate"):
            # Recurring (rule-generated) tasks: /app/task/complete returns 200 but the
            # day's instance isn't marked; refuse rather than flip hidden state.
            raise HTTPException(409, "recurring chore: complete it in the eCalendar app")
        res = run(lambda t: ecal.api("/app/task/complete",
                                     {"deviceId": str(ecal.DEVICE), "eventId": eid}, t))
        cache.clear()
        data = (res or {}).get("data") or {}
        return {"eventId": eid, "done": bool(data.get("completedStatus")), **chore_payload(day)}

    def _allowed():
        cats = cache.get_or_set("categories", lambda: run_token(lambda t: ecal.list_categories(t)))
        return add_people(cats)

    @r.post("/dashboard/api/events/create")
    def event_create(body: dict = Body(...)):
        allowed = _allowed()
        spec = event_spec(body, allowed)
        res = run_token(lambda t: ecal.create_event(tok=t, **spec))
        cache.clear()
        names = {p["id"]: p["name"] for p in allowed}
        return {"ok": (res or {}).get("code") == 200, "title": spec["title"],
                "start": spec["start"], "end": spec.get("end"), "allDay": spec["all_day"],
                "profiles": [names.get(c, c) for c in spec["category"]]}

    @r.post("/dashboard/api/chores/create")
    def chore_create(body: dict = Body(...)):
        allowed = _allowed()
        spec = chore_spec(body, allowed)
        names = {p["id"]: p["name"] for p in allowed}
        made = []
        for cat in spec["categories"]:  # the API takes one profile per chore
            res = run_token(lambda t, c=cat: ecal.api("/app/task", chore_body(spec, c), t))
            ids = ((res or {}).get("data") or {}).get("eventIds") or []
            made.append({"profile": names.get(cat, cat), "eventIds": [str(i) for i in ids]})
        cache.clear()
        return {"ok": True, "title": spec["title"], "date": spec["due"], "stars": spec["stars"],
                "time": spec["time"], "created": made}

    @r.get("/dashboard/api/events")
    def events(start: str = Query(...), end: str = Query(...)):
        s, e = parse_day(start, "start"), parse_day(end, "end")
        if e < s or (e - s).days > 62:
            raise HTTPException(400, "range must be 0-62 days")
        rows = cache.get_or_set(
            f"events:{s}:{e}", lambda: run(lambda t: ecal.list_events(s.isoformat(), e.isoformat(), t)))
        out = []
        for row in rows:
            ev = normalize(row)
            if not ev:
                continue
            d0 = ev["start"][:10]
            d1 = ev["end"][:10] if ev["allDay"] else d0
            if d1 >= s.isoformat() and d0 <= e.isoformat():
                out.append(ev)
        out.sort(key=lambda x: (x["start"][:10], not x["allDay"], x["start"]))
        return {"start": s.isoformat(), "end": e.isoformat(), "events": out}

    @r.get("/dashboard/api/home")
    def home():
        return get_home()

    @r.get("/dashboard/api/weather")
    def weather():
        return get_weather()

    ring = {"last_ring": None, "source": None}

    def _ok(given, secret):
        return bool(secret) and bool(given) and hmac.compare_digest(given, secret)

    def _mark(source):
        ring["last_ring"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ring["source"] = source
        return dict(ring)

    ha_ring = {"seen": None, "at": 0.0}

    def _poll_ha_ring():
        # HA event entity (e.g. UniFi Protect event.doorbell_doorbell): its state becomes a new
        # timestamp on each ring. Read-only GET, cached ~1.5s; first value seen is the baseline.
        eid = os.environ.get("DASHBOARD_DOORBELL_ENTITY", "event.doorbell_doorbell").strip()
        url, tok = os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "")
        if not (eid and url and tok) or time.time() - ha_ring["at"] < 1.5:
            return
        ha_ring["at"] = time.time()
        try:
            req = urllib.request.Request(f"{url}/api/states/{eid}", headers={"Authorization": "Bearer " + tok})
            with urllib.request.urlopen(req, timeout=3) as f:
                st = str(json.loads(f.read().decode()).get("state"))
        except Exception:
            return
        if ha_ring["seen"] is not None and st != ha_ring["seen"] and st not in ("unknown", "unavailable", "None"):
            _mark("ha")
        ha_ring["seen"] = st

    @r.get("/dashboard/api/doorbell")
    def doorbell():
        _poll_ha_ring()
        return {**ring, "now": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    @r.post("/dashboard/api/doorbell/ring")
    def doorbell_ring(x_doorbell_secret: Optional[str] = Header(default=None)):
        if not _ok(x_doorbell_secret, os.environ.get("DASHBOARD_DOORBELL_SECRET", "")):
            raise HTTPException(401, "bad or missing X-Doorbell-Secret")
        return _mark("ring")

    @r.post("/dashboard/api/doorbell/test")
    def doorbell_test(x_api_key: Optional[str] = Header(default=None)):
        if not _ok(x_api_key, os.environ.get("GATEWAY_API_KEY", "")):
            raise HTTPException(401, "bad or missing X-API-Key")
        return _mark("test")

    return r


# ---------------------------------------------------------------- NWS weather
_WX = {"at": 0.0, "data": None, "grid": None}
_WX_LOCK = threading.Lock()
WX_TTL = 15 * 60


def _nws(url):
    ua = os.environ.get("DASHBOARD_WEATHER_UA", "BakerFamilyDashboard/1.0 (you@example.com)")
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=15) as f:
        return json.loads(f.read().decode())


def wx_icon(text, day=True):
    t = (text or "").lower()
    for keys, ic in ((("thunder",), "⛈️"), (("snow", "flurr", "blizzard"), "🌨️"),
                     (("sleet", "freezing", "ice"), "🌧️"), (("rain", "shower", "drizzle"), "🌧️"),
                     (("fog", "haze", "smoke"), "🌫️"), (("mostly cloudy", "overcast", "cloudy"), "☁️"),
                     (("partly",), "⛅"), (("mostly sunny", "mostly clear"), "🌤️" if day else "🌙"),
                     (("sunny", "clear", "fair"), "☀️" if day else "🌙"), (("wind", "breezy"), "💨")):
        if any(k in t for k in keys):
            if ic == "☁️" and "partly" in t:
                continue
            return ic
    return "🌡️"


def parse_forecast(periods):
    days = {}
    for p in periods:
        d = (p.get("startTime") or "")[:10]
        k = days.setdefault(d, {"date": d, "hi": None, "lo": None, "icon": None, "text": None})
        if p.get("isDaytime"):
            k["hi"] = p.get("temperature")
            k["icon"], k["text"] = wx_icon(p.get("shortForecast")), p.get("shortForecast")
        else:
            k["lo"] = p.get("temperature")
            if k["icon"] is None:
                k["icon"], k["text"] = wx_icon(p.get("shortForecast"), False), p.get("shortForecast")
    return sorted(days.values(), key=lambda x: x["date"])


def fetch_weather():
    lat = float(os.environ.get("DASHBOARD_LAT", "45.0725"))
    lon = float(os.environ.get("DASHBOARD_LON", "-93.4558"))
    if not _WX["grid"]:
        pt = _nws(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")["properties"]
        _WX["grid"] = (pt["forecast"], pt["observationStations"])
    fc_url, st_url = _WX["grid"]
    periods = _nws(fc_url)["properties"]["periods"]
    days = parse_forecast(periods)
    cur = {"temp": None, "text": None, "icon": None}
    try:
        st = _nws(st_url)["features"][0]["properties"]["stationIdentifier"]
        ob = _nws(f"https://api.weather.gov/stations/{st}/observations/latest")["properties"]
        c = (ob.get("temperature") or {}).get("value")
        if c is not None:
            cur["temp"] = round(c * 9 / 5 + 32)
        cur["text"] = ob.get("textDescription")
    except Exception:
        pass
    if cur["temp"] is None and periods:
        cur["temp"] = periods[0].get("temperature")
    if not cur["text"] and periods:
        cur["text"] = periods[0].get("shortForecast")
    cur["icon"] = wx_icon(cur["text"], bool(periods and periods[0].get("isDaytime")))
    return {"current": cur, "days": days, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def get_weather():
    with _WX_LOCK:
        if _WX["data"] and time.time() - _WX["at"] < WX_TTL:
            return _WX["data"]
        try:
            _WX["data"], _WX["at"] = fetch_weather(), time.time()
        except Exception as e:
            if _WX["data"]:
                return {**_WX["data"], "stale": True}
            raise HTTPException(502, f"weather unavailable: {type(e).__name__}")
        return _WX["data"]


# ---------------------------------------------------------------- Home Assistant chips
_HA = {"at": 0.0, "data": None}
_HA_LOCK = threading.Lock()
HA_TTL = 10

ATTENTION = {"cover": {"open", "opening", "closing"}, "lock": {"unlocked", "open", "jammed", "unlocking"},
             "binary_sensor": {"on"}, "alarm_control_panel": {"triggered", "pending", "arming"}}
ICONS = {"cover": "🚗", "lock": "🔒", "binary_sensor": "🚪", "person": "👤", "device_tracker": "📱",
         "alarm_control_panel": "🛡️"}


def ha_chip(st):
    eid = st.get("entity_id", "")
    dom = eid.split(".")[0]
    state = str(st.get("state"))
    a = st.get("attributes") or {}
    name = a.get("friendly_name") or eid
    if dom in ("person", "device_tracker"):
        name = name.split()[0]
        label = "Home" if state == "home" else ("Away" if state == "not_home" else state.replace("_", " ").title())
        level = "ok" if state == "home" else "info"
    elif dom == "binary_sensor" and a.get("device_class") in ("door", "garage_door", "window", "opening"):
        label = {"on": "Open", "off": "Closed"}.get(state, state.title())
        level = "warn" if state == "on" else "ok"
    else:
        label = state.replace("_", " ").title()
        level = "warn" if state in ATTENTION.get(dom, set()) else "ok"
    if state in ("unavailable", "unknown"):
        level, label = "info", "?"
    icon = ICONS.get(dom, "•")
    if dom == "lock" and level == "warn":
        icon = "🔓"
    return {"id": eid, "name": name, "state": state, "label": label, "level": level, "icon": icon}


def fetch_home():
    url, tok = os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "")
    if not url or not tok:
        return {"chips": [], "configured": False}
    # comma list; each item is entity_id or entity_id=Friendly label
    want = [e.strip().split("=", 1) for e in os.environ.get("DASHBOARD_HA_ENTITIES", "person.kyle_baker").split(",") if e.strip()]
    want = [(w[0].strip(), w[1].strip() if len(w) > 1 else None) for w in want]
    req = urllib.request.Request(url + "/api/states", headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=8) as f:
        states = {s.get("entity_id"): s for s in json.loads(f.read().decode())}
    return {"configured": True, "chips": [{**ha_chip(states[e]), **({"name": n} if n else {})} for e, n in want if e in states]}


def get_home():
    with _HA_LOCK:
        if _HA["data"] is not None and time.time() - _HA["at"] < HA_TTL:
            return _HA["data"]
        try:
            _HA["data"], _HA["at"] = fetch_home(), time.time()
        except Exception as e:
            if _HA["data"] is not None:
                return {**_HA["data"], "stale": True}
            raise HTTPException(502, f"home assistant unavailable: {type(e).__name__}")
        return _HA["data"]
