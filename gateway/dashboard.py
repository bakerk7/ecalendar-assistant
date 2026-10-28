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
  DASHBOARD_HA_ENTITIES    comma list of entity_ids shown as header chips (default person.kyle_baker)
  DASHBOARD_CHORE_PEOPLE   comma list of profiles always shown in chores (default Jack,Liv)
"""
from __future__ import annotations

import hmac
import json
import os
import threading
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


def make_router(run, cache, parse_day) -> APIRouter:
    r = APIRouter(include_in_schema=False)

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

    @r.get("/dashboard/api/doorbell")
    def doorbell():
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
    want = [e.strip() for e in os.environ.get("DASHBOARD_HA_ENTITIES", "person.kyle_baker").split(",") if e.strip()]
    req = urllib.request.Request(url + "/api/states", headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=8) as f:
        states = {s.get("entity_id"): s for s in json.loads(f.read().decode())}
    return {"configured": True, "chips": [ha_chip(states[e]) for e in want if e in states]}


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
