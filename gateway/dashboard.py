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
  DASHBOARD_LAT/LON/PLACE  weather location (default Maple Grove, MN)
"""
from __future__ import annotations

import hmac
import os
from typing import Optional
from datetime import datetime, timezone
from datetime import timedelta

from fastapi import APIRouter, Header, HTTPException, Query
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
        }

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
