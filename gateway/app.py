"""eCalendar gateway: one shared eCalendar session behind a small LAN HTTP API.

The eCalendar account allows a single login session -- every new sign-in issues
a new token and invalidates the previous one ({"code":401,"msg":"Account logged
in on another device"}). This service holds that one token so a wall-screen UI
and sync scripts can share it instead of each logging in.

Config (env):
  ECALENDAR_TOKEN              starting token (preferred; no login needed)
  ECAL_EMAIL / ECAL_PASSWORD   optional; used ONLY to re-login after a 401
  ECALENDAR_DEVICE, ECALENDAR_TIMEZONE, ECALENDAR_CATEGORIES ...  as for ecal.py
  GATEWAY_API_KEY              optional shared secret, sent as X-API-Key
  GATEWAY_CACHE_TTL            read cache seconds (default 60)
  GATEWAY_RELOGIN_MIN_BACKOFF  first re-login backoff seconds (default 60)
  GATEWAY_RELOGIN_MAX_BACKOFF  cap (default 1800)
  GATEWAY_HOST / GATEWAY_PORT  bind (default 0.0.0.0:8080; used by __main__)
"""
from __future__ import annotations

import hmac
import logging
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ecal  # noqa: E402

log = logging.getLogger("ecal-gateway")


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


class SessionUnavailable(RuntimeError):
    """No valid token right now (replaced and re-login not possible / backing off)."""


class Session:
    """Holds the single eCalendar token. Re-logs in only after a 401, with backoff."""

    def __init__(self, token=None, email=None, password=None,
                 min_backoff=60.0, max_backoff=1800.0, clock=time.monotonic,
                 login_fn=None):
        self.token = (token or "").strip() or None
        self.email = email or None
        self.password = password or None
        self.min_backoff = min_backoff
        self.max_backoff = max_backoff
        self._clock = clock
        self._login_fn = login_fn or ecal.login
        self._lock = threading.Lock()
        self._backoff = min_backoff
        self._next_login_at = 0.0
        self.last_replaced_at: Optional[str] = None
        self.last_login_at: Optional[str] = None
        self.last_error: Optional[str] = None
        self.relogins = 0

    @property
    def can_login(self):
        return bool(self.email and self.password)

    def status(self):
        return {
            "has_token": bool(self.token),
            "can_relogin": self.can_login,
            "relogins": self.relogins,
            "last_replaced_at": self.last_replaced_at,
            "last_login_at": self.last_login_at,
            "last_error": self.last_error,
            "next_login_allowed_in_s": max(0, round(self._next_login_at - self._clock())),
        }

    def _login_locked(self):
        now = self._clock()
        if now < self._next_login_at:
            raise SessionUnavailable(
                f"re-login backing off for {self._next_login_at - now:.0f}s")
        try:
            log.warning("logging in to eCalendar as %s -- this will kick out any other "
                        "session (phone/Mac app, other scripts)", self.email)
            self.token = self._login_fn(self.email, self.password)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"login failed: {type(e).__name__}: {e}"
            self._next_login_at = now + self._backoff
            log.error("%s; next attempt in %.0fs", self.last_error, self._backoff)
            self._backoff = min(self._backoff * 2, self.max_backoff)
            raise SessionUnavailable(self.last_error) from e
        self.relogins += 1
        self.last_login_at = _now_iso()
        self.last_error = None
        # Successful login: keep a floor so two devices can't ping-pong us into a storm.
        self._next_login_at = now + self.min_backoff
        self._backoff = self.min_backoff
        log.info("eCalendar login OK (relogin #%d)", self.relogins)

    def get_token(self):
        with self._lock:
            if not self.token:
                if not self.can_login:
                    raise SessionUnavailable("no ECALENDAR_TOKEN and no ECAL_EMAIL/ECAL_PASSWORD")
                self._login_locked()
            return self.token

    def call(self, fn: Callable[[str], Any]):
        """Run fn(token). On a replaced token, re-login once (if allowed) and retry."""
        tok = self.get_token()
        try:
            return fn(tok)
        except ecal.TokenReplacedError as e:
            with self._lock:
                if self.token == tok:  # nobody refreshed it while we were calling
                    self.last_replaced_at = _now_iso()
                    log.warning("eCalendar session was taken by another device/login "
                                "(401: %s)", e)
                    if not self.can_login:
                        self.last_error = "token replaced by another login; set a new ECALENDAR_TOKEN"
                        self.token = None
                        raise SessionUnavailable(self.last_error) from e
                    self._login_locked()
                new_tok = self.token
            return fn(new_tok)


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class TTLCache:
    def __init__(self, ttl, clock=time.monotonic):
        self.ttl, self._clock, self._d, self._lock = ttl, clock, {}, threading.Lock()

    def get_or_set(self, key, fn):
        if self.ttl > 0:
            with self._lock:
                hit = self._d.get(key)
                if hit and hit[0] > self._clock():
                    return hit[1]
        val = fn()
        if self.ttl > 0:
            with self._lock:
                self._d[key] = (self._clock() + self.ttl, val)
        return val

    def clear(self):
        with self._lock:
            self._d.clear()


def _parse_day(s, name):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name} must be YYYY-MM-DD")


def create_app(session: Optional[Session] = None, cache_ttl: Optional[float] = None,
               api_key: Optional[str] = None) -> FastAPI:
    ecal._refresh_from_env()
    if session is None:
        session = Session(
            token=os.environ.get("ECALENDAR_TOKEN"),
            email=os.environ.get("ECAL_EMAIL"), password=os.environ.get("ECAL_PASSWORD"),
            min_backoff=_env_float("GATEWAY_RELOGIN_MIN_BACKOFF", 60),
            max_backoff=_env_float("GATEWAY_RELOGIN_MAX_BACKOFF", 1800))
    if cache_ttl is None:
        cache_ttl = _env_float("GATEWAY_CACHE_TTL", 60)
    if api_key is None:
        api_key = os.environ.get("GATEWAY_API_KEY", "")
    cache = TTLCache(cache_ttl)

    def auth(x_api_key: Optional[str] = Header(default=None)):
        if api_key and not (x_api_key and hmac.compare_digest(x_api_key, api_key)):
            raise HTTPException(401, "missing or bad X-API-Key")

    app = FastAPI(title="eCalendar gateway", version="0.1.0")
    app.state.session, app.state.cache = session, cache

    def run(fn):
        try:
            return session.call(fn)
        except SessionUnavailable as e:
            raise HTTPException(503, f"eCalendar session unavailable: {e}")
        except ecal.TokenReplacedError:
            raise HTTPException(503, "eCalendar token was replaced again right after re-login")
        except RuntimeError as e:  # API code != 200 -> code/msg only, no payload
            raise HTTPException(502, str(e))
        except (OSError, TimeoutError) as e:
            raise HTTPException(504, f"upstream error: {type(e).__name__}")

    @app.get("/health")
    def health():
        # No upstream call: liveness only. Session state is informational.
        return {"ok": True, "session": session.status()}

    @app.get("/whoami", dependencies=[Depends(auth)])
    def whoami():
        d = cache.get_or_set("whoami", lambda: run(lambda t: ecal.whoami(t)))
        keys = ("userId", "userName", "email", "plusType", "isSubscribe")
        return {k: d.get(k) for k in keys if k in d}

    @app.get("/categories", dependencies=[Depends(auth)])
    @app.get("/profiles", dependencies=[Depends(auth)], include_in_schema=False)
    def categories():
        rows = cache.get_or_set("categories", lambda: run(lambda t: ecal.list_categories(t)))
        return [{
            "id": str(r.get("userCalendarCategoryId")),
            "name": r.get("categoryName"),
            "color": r.get("categoryColor"),
            "type": r.get("categoryType"),
            "external": r.get("syncCalenderId") is not None,
        } for r in rows]

    @app.get("/events", dependencies=[Depends(auth)])
    def events(start: str = Query(..., description="YYYY-MM-DD"),
               end: str = Query(..., description="YYYY-MM-DD, inclusive")):
        s, e = _parse_day(start, "start"), _parse_day(end, "end")
        if e < s:
            raise HTTPException(400, "end before start")
        if (e - s).days > 366 * 2:
            raise HTTPException(400, "range too large (max ~2 years)")
        key = f"events:{s}:{e}"
        rows = cache.get_or_set(
            key, lambda: run(lambda t: ecal.list_events(s.isoformat(), e.isoformat(), t)))
        # list_events pads +/-1 local day; trim to the requested window here.
        out = []
        for r in rows:
            loc = ecal.event_local_start(r)
            if loc is None or s <= loc.date() <= e:
                out.append(r)
        return {"start": s.isoformat(), "end": e.isoformat(), "count": len(out), "rows": out}

    def write(path, body):
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object body required")
        body = dict(body)
        body.setdefault("deviceId", str(ecal.DEVICE))
        res = run(lambda t: ecal.api(path, body, t))
        cache.clear()
        return {"code": res.get("code"), "msg": res.get("msg"), "data": res.get("data")}

    # Pass-through writes: body is the raw eCalendar API body (see ecalendar-api.md).
    @app.post("/events", dependencies=[Depends(auth)])
    def create_event(body: dict = Body(...)):
        return write("/app/event/add", body)

    @app.put("/events/{event_id}", dependencies=[Depends(auth)])
    def update_event(event_id: str, body: dict = Body(...)):
        return write("/app/event/edit/selective", {**body, "eventId": event_id})

    @app.delete("/events/{event_id}", dependencies=[Depends(auth)])
    def delete_event(event_id: str, series: bool = False,
                     body: Optional[dict] = Body(default=None)):
        b = {"deleteMethod": 2 if series else 0, "originEventId": None,
             "originStartDatetime": None, "originEndDatetime": None}
        b.update(body or {})
        b["eventId"] = event_id
        return write("/app/event/delete/selective", b)

    @app.post("/cache/clear", dependencies=[Depends(auth)])
    def clear_cache():
        cache.clear()
        return {"ok": True}

    return app


def main():
    import uvicorn
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host=os.environ.get("GATEWAY_HOST", "0.0.0.0"),
                port=int(os.environ.get("GATEWAY_PORT", "8080")))


if __name__ == "__main__":
    main()
