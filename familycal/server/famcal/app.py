"""HTTP API for the tablet app + phone web page + ecal-gateway-compatible /events."""
import hmac, json, logging, os, threading, time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .core import Service
from .db import DB, new_id, now
from .sources import TZ, make_source
from . import oauth
from .doors import Doors

log = logging.getLogger("famcal")
STATIC = os.path.join(os.path.dirname(__file__), "static")
UTC = ZoneInfo("UTC")


def _day(s, name="date"):
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name} must be YYYY-MM-DD")


def create_app(svc: Optional[Service] = None, api_key: Optional[str] = None, data_dir=None,
               start_sync=True) -> FastAPI:
    data_dir = data_dir or os.environ.get("FAMCAL_DATA", "/data")
    if svc is None:
        svc = Service(DB(os.path.join(data_dir, "famcal.db")), make_source(data_dir))
    if api_key is None:
        api_key = os.environ.get("FAMCAL_API_KEY", "")
    app = FastAPI(title="Family Calendar helper", version="1.0.0")

    class _MethodOverride:
        """Android's HttpURLConnection can't send PATCH: accept POST + X-HTTP-Method-Override."""
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["method"] == "POST":
                for k, v in scope.get("headers", []):
                    if k == b"x-http-method-override" and v.upper() in (b"PATCH", b"PUT", b"DELETE"):
                        scope = dict(scope, method=v.decode().upper())
                        break
            await self.inner(scope, receive, send)

    app.add_middleware(_MethodOverride)
    app.state.svc = svc
    db = svc.db

    def auth(request: Request, x_api_key: Optional[str] = Header(default=None)):
        k = x_api_key or request.query_params.get("key") or request.cookies.get("famcal_key")
        if api_key and not (k and hmac.compare_digest(k, api_key)):
            raise HTTPException(401, "missing or bad API key")

    A = [Depends(auth)]

    def nf(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except KeyError as e:
            raise HTTPException(404, f"not found: {e}")
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------------------ basics
    @app.get("/health")
    def health():
        return {"ok": True, "source": svc.source.name, "last_sync": svc.last_sync,
                "sync_error": bool(svc.last_sync_error)}

    @app.get("/", response_class=HTMLResponse)
    def page():
        return FileResponse(os.path.join(STATIC, "index.html"))

    @app.get("/api/status", dependencies=A)
    def status():
        return {"source": svc.source.status(), "last_sync": svc.last_sync, "sync_error": svc.last_sync_error,
                "events": db.one("SELECT COUNT(*) n FROM events WHERE deleted=0")["n"],
                "ecalendar": {"last": getattr(getattr(svc, "ecal_feed", None), "last", None), "error": getattr(getattr(svc, "ecal_feed", None), "error", None), "stats": getattr(getattr(svc, "ecal_feed", None), "stats", None), "shown": db.one("SELECT COUNT(*) n FROM events WHERE deleted=0 AND origin LIKE 'ecal-native%'")["n"], "hidden_duplicates": db.one("SELECT COUNT(*) n FROM events WHERE deleted=2")["n"]},
                "to_tag": len(svc.to_tag(1000)), "calendars": svc.calendars()}

    @app.post("/api/sync", dependencies=A)
    def sync_now():
        return {"changed": svc.sync(), "error": svc.last_sync_error}

    @app.get("/api/profiles", dependencies=A)
    def profiles():
        return svc.profiles()

    @app.patch("/api/profiles/{pid}", dependencies=A)
    def edit_profile(pid: str, body: dict = Body(...)):
        for k in ("name", "color", "emoji"):
            if k in body:
                db.x(f"UPDATE profiles SET {k}=? WHERE id=?", (body[k], pid))
        return svc.profiles()

    # ------------------------------------------------------------ events
    @app.get("/api/instances", dependencies=A)
    def instances(start: str, end: str):
        s, e = _day(start, "start"), _day(end, "end")
        if e < s or (e - s).days > 120:
            raise HTTPException(400, "bad range (max 120 days)")
        return {"start": s.isoformat(), "end": e.isoformat(), "items": svc.instances(s, e)}

    @app.get("/api/events/{eid}", dependencies=A)
    def get_event(eid: str):
        ev = svc.event(eid)
        if not ev:
            raise HTTPException(404, "not found")
        return ev

    def _validate(body, partial=False):
        if not partial and not (body.get("title") or "").strip():
            raise HTTPException(400, "title required")
        for k in ("start", "end"):
            if body.get(k):
                try:
                    datetime.fromisoformat(body[k] if "T" in body[k] else body[k] + "T00:00")
                except ValueError:
                    raise HTTPException(400, f"{k} must be YYYY-MM-DD or YYYY-MM-DDTHH:MM")
        if not partial and not body.get("start"):
            raise HTTPException(400, "start required")

    @app.post("/api/events", dependencies=A)
    def create_event(body: dict = Body(...)):
        _validate(body)
        return nf(svc.create_event, body, body.get("cal", "shared"), body.get("creator"))

    @app.patch("/api/events/{eid}", dependencies=A)
    def update_event(eid: str, body: dict = Body(...)):
        _validate(body, partial=True)
        return nf(svc.update_event, eid, body)

    @app.delete("/api/events/{eid}", dependencies=A)
    def delete_event(eid: str):
        nf(svc.delete_event, eid)
        return {"ok": True}

    @app.put("/api/events/{eid}/tags", dependencies=A)
    def put_tags(eid: str, body: dict = Body(...)):
        prof = [p for p in body.get("profiles", []) if p != "family"]
        return nf(svc.set_tags, eid, prof)

    @app.get("/api/totag", dependencies=A)
    def totag():
        return svc.to_tag()

    # ------------------------------------------------------------ rules
    @app.get("/api/rules", dependencies=A)
    def get_rules():
        return [dict(r, profiles=json.loads(r["profiles"])) for r in db.q("SELECT * FROM rules ORDER BY priority,id")]

    @app.post("/api/rules", dependencies=A)
    def add_rule(body: dict = Body(...)):
        if body.get("kind") not in ("keyword", "calendar", "creator"):
            raise HTTPException(400, "kind must be keyword|calendar|creator")
        if body["kind"] == "keyword":
            import re
            try:
                re.compile(body.get("pattern", ""))
            except re.error:
                raise HTTPException(400, "bad pattern")
        db.x("INSERT INTO rules(kind,pattern,profiles,priority,note) VALUES(?,?,?,?,?)",
             (body["kind"], body.get("pattern", ""), json.dumps(body.get("profiles", [])),
              int(body.get("priority", 70)), body.get("note")))
        return get_rules()

    @app.delete("/api/rules/{rid}", dependencies=A)
    def del_rule(rid: int):
        db.x("DELETE FROM rules WHERE id=?", (rid,))
        return get_rules()

    # ------------------------------------------------------------ chores / stars / rewards
    @app.get("/api/chores", dependencies=A)
    def chores(d: Optional[str] = Query(None, alias="date")):
        return svc.chores_for(_day(d) if d else date.today())

    @app.get("/api/chores/all", dependencies=A)
    def chores_all():
        return db.q("SELECT c.*, m.time, m.grp FROM chores c LEFT JOIN chore_meta m ON m.chore_id=c.id WHERE c.active=1 ORDER BY c.profile_id, c.sort, c.title")

    @app.post("/api/chores", dependencies=A)
    def add_chore(body: dict = Body(...)):
        pids = body.get("profile_ids") or [body.get("profile_id")]
        if not (body.get("title") or "").strip() or any(p not in svc.valid_ids() for p in pids):
            raise HTTPException(400, "title and valid profile_id(s) required")
        if body.get("days") and (len(body["days"]) != 7 or set(body["days"]) - {"0", "1"}):
            raise HTTPException(400, "days must be 7 chars of 0/1 (Mon..Sun)")
        out = svc.add_chores(body)
        return out if body.get("profile_ids") else out[0]

    @app.patch("/api/chores/{cid}", dependencies=A)
    def edit_chore(cid: str, body: dict = Body(...)):
        if not db.one("SELECT 1 FROM chores WHERE id=?", (cid,)):
            raise HTTPException(404, "not found")
        for k in ("title", "emoji", "profile_id", "stars", "days", "start_date", "end_date", "once_date", "active", "sort"):
            if k in body:
                db.x(f"UPDATE chores SET {k}=? WHERE id=?", (body[k], cid))
        if "time" in body:
            m = db.one("SELECT grp FROM chore_meta WHERE chore_id=?", (cid,)) or {}
            db.x("INSERT OR REPLACE INTO chore_meta(chore_id,time,grp) VALUES(?,?,?)", (cid, body["time"], m.get("grp")))
        r = db.one("SELECT c.*, m.time, m.grp FROM chores c LEFT JOIN chore_meta m ON m.chore_id=c.id WHERE c.id=?", (cid,))
        return r

    @app.delete("/api/chores/{cid}", dependencies=A)
    def del_chore(cid: str):
        db.x("UPDATE chores SET active=0 WHERE id=?", (cid,))
        return {"ok": True}

    @app.post("/api/chores/{cid}/toggle", dependencies=A)
    def toggle(cid: str, body: dict = Body(default={})):
        d = _day(body["date"]) if body.get("date") else date.today()
        return nf(svc.toggle_chore, cid, d, body.get("done"))

    @app.get("/api/stars", dependencies=A)
    def stars():
        return {p["id"]: p["stars"] for p in svc.profiles(False)}

    @app.get("/api/stars/history", dependencies=A)
    def stars_hist(profile: Optional[str] = None, limit: int = 50):
        if profile:
            return db.q("SELECT * FROM ledger WHERE profile_id=? ORDER BY id DESC LIMIT ?", (profile, limit))
        return db.q("SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,))

    @app.post("/api/stars/adjust", dependencies=A)
    def adjust(body: dict = Body(...)):
        if body.get("profile_id") not in svc.valid_ids():
            raise HTTPException(400, "bad profile")
        db.x("INSERT INTO ledger(profile_id,delta,reason,ref,ts) VALUES(?,?,?,?,?)",
             (body["profile_id"], int(body.get("delta", 0)), body.get("reason", "manual adjust"), "adjust", now()))
        return {"stars": svc.balance(body["profile_id"])}

    @app.get("/api/rewards", dependencies=A)
    def rewards():
        return db.q("SELECT * FROM rewards WHERE active=1 ORDER BY cost")

    @app.post("/api/rewards", dependencies=A)
    def add_reward(body: dict = Body(...)):
        if not body.get("title") or int(body.get("cost", 0)) <= 0:
            raise HTTPException(400, "title and positive cost required")
        rid = new_id()
        db.x("INSERT INTO rewards(id,title,emoji,cost,profile_id) VALUES(?,?,?,?,?)",
             (rid, body["title"], body.get("emoji", "🎁"), int(body["cost"]), body.get("profile_id")))
        return db.one("SELECT * FROM rewards WHERE id=?", (rid,))

    @app.delete("/api/rewards/{rid}", dependencies=A)
    def del_reward(rid: str):
        db.x("UPDATE rewards SET active=0 WHERE id=?", (rid,))
        return {"ok": True}

    @app.post("/api/rewards/{rid}/redeem", dependencies=A)
    def redeem(rid: str, body: dict = Body(...)):
        return nf(svc.redeem, rid, body.get("profile_id"))

    # ------------------------------------------------------------ lists
    @app.get("/api/lists", dependencies=A)
    def lists():
        ls = db.q("SELECT * FROM lists ORDER BY sort, name")
        for l in ls:
            l["items"] = db.q("SELECT * FROM list_items WHERE list_id=? ORDER BY done, sort, rowid", (l["id"],))
        return ls

    @app.post("/api/lists", dependencies=A)
    def add_list(body: dict = Body(...)):
        if not body.get("name"):
            raise HTTPException(400, "name required")
        db.x("INSERT INTO lists(id,name,emoji,sort) VALUES(?,?,?,?)", (new_id(), body["name"], body.get("emoji", "📝"), 50))
        return lists()

    @app.patch("/api/lists/{lid}", dependencies=A)
    def edit_list(lid: str, body: dict = Body(...)):
        if not db.one("SELECT 1 FROM lists WHERE id=?", (lid,)):
            raise HTTPException(404, "not found")
        for k in ("name", "emoji", "sort"):
            if k in body:
                if k == "name" and not (body[k] or "").strip():
                    raise HTTPException(400, "name required")
                db.x(f"UPDATE lists SET {k}=? WHERE id=?", (body[k], lid))
        return lists()

    @app.delete("/api/lists/{lid}", dependencies=A)
    def del_list(lid: str):
        db.x("DELETE FROM list_items WHERE list_id=?", (lid,))
        db.x("DELETE FROM lists WHERE id=?", (lid,))
        return {"ok": True}

    @app.post("/api/lists/{lid}/items", dependencies=A)
    def add_item(lid: str, body: dict = Body(...)):
        if not (body.get("text") or "").strip():
            raise HTTPException(400, "text required")
        iid = new_id()
        db.x("INSERT INTO list_items(id,list_id,text,profile_id,updated) VALUES(?,?,?,?,?)",
             (iid, lid, body["text"].strip(), body.get("profile_id"), now()))
        return db.one("SELECT * FROM list_items WHERE id=?", (iid,))

    @app.patch("/api/items/{iid}", dependencies=A)
    def edit_item(iid: str, body: dict = Body(...)):
        for k in ("text", "done", "sort", "profile_id"):
            if k in body:
                db.x(f"UPDATE list_items SET {k}=?, updated=? WHERE id=?", (body[k], now(), iid))
        return db.one("SELECT * FROM list_items WHERE id=?", (iid,))

    @app.delete("/api/items/{iid}", dependencies=A)
    def del_item(iid: str):
        db.x("DELETE FROM list_items WHERE id=?", (iid,))
        return {"ok": True}

    @app.post("/api/lists/{lid}/clear-done", dependencies=A)
    def clear_done(lid: str):
        db.x("DELETE FROM list_items WHERE list_id=? AND done=1", (lid,))
        return {"ok": True}

    # ------------------------------------------------------------ weather + tablet bundle
    doors = Doors(ttl=1.5)  # app polls /api/doors every ~3 s; HA reads are 3 tiny GETs
    app.state.doors = doors

    @app.get("/api/doors", dependencies=A)
    def doors_status():
        return doors.status()

    @app.post("/api/doors/fake", dependencies=A)
    def doors_fake(body: dict = Body(...)):
        """Testing only: {"name": "Garage", "minutes": 12} fakes an open door; minutes null clears it. Never writes to HA."""
        if body.get("clear_all"):
            doors.fake.clear()
        elif body.get("name"):
            doors.set_fake(body["name"], body.get("minutes"))
        return doors.status()

    @app.get("/api/weather", dependencies=A)
    def weather():
        return svc.weather()

    @app.get("/api/home", dependencies=A)
    def home(start: str, end: str, chores_date: Optional[str] = None):
        """Everything the tablet needs in one round trip (cuts radio wakeups + memory churn)."""
        s, e = _day(start, "start"), _day(end, "end")
        if e < s or (e - s).days > 120:
            raise HTTPException(400, "bad range")
        return {"profiles": svc.profiles(), "items": svc.instances(s, e), "chores": svc.chores_for(_day(chores_date) if chores_date else date.today()), "reminders": svc.upcoming_reminders(),
                "calendars": [{"id": c["id"], "name": c["name"], "color": c.get("color"),
                               "writable": bool(c.get("writable", 1))} for c in svc.calendars()],
                "rewards": rewards(), "lists": lists(), "weather": svc.weather(),
                "to_tag": len(svc.to_tag(1000)), "server_time": now(),
                "doors": doors.status()["doors"] if doors.cfg().get("sensors") else []}

    # ------------------------------------------------------------ Google OAuth (one-time)
    app.include_router(oauth.router(data_dir, A, svc))

    # ------------------------------------------------------------ ecal-gateway compatible API
    # Same paths/shape as ~/docker-stacks/ecalendar-assistant/gateway (/events, /profiles):
    # routines can switch by changing only the base URL.
    def _ecal_map():
        m = {}
        for p in svc.profiles(False):
            m[p["id"]] = p["id"]; m[p["name"].lower()] = p["id"]
            if p.get("ecal_id"):
                m[str(p["ecal_id"])] = p["id"]
        for c in svc.calendars():
            if c.get("ecal_id"):
                m[str(c["ecal_id"])] = "cal:" + c["id"]
        return m

    def _local_to_utc(s):
        return datetime.fromisoformat(s).replace(tzinfo=TZ).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")

    def _utc_to_local(s):
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).astimezone(TZ).strftime("%Y-%m-%dT%H:%M")

    def _compat_row(it, rev):
        if it["all_day"]:
            sd, ed = it["start"][:10] + " 00:00:00", (it["end"] or it["start"])[:10] + " 23:59:59"
        else:
            sd, ed = _local_to_utc(it["start"]), _local_to_utc(it["end"] or it["start"])
        ids = [rev.get(p, p) for p in it["profiles"]]
        return {"eventId": it["id"], "title": it["title"], "eventType": 0, "isAllDay": int(it["all_day"]),
                "startDatetime": sd, "endDatetime": ed, "isRecurring": int(it["recurring"]),
                "description": it["description"], "userCalendarCategoryIds": ids,
                "profiles": it["profiles"], "localStart": it["start"], "localEnd": it["end"]}

    @app.get("/categories", dependencies=A)
    @app.get("/profiles", dependencies=A, include_in_schema=False)
    def compat_profiles():
        return [{"id": str(p.get("ecal_id") or p["id"]), "name": p["name"], "color": p["color"], "type": "1",
                 "external": False, "profileId": p["id"]} for p in svc.profiles(False)]

    @app.get("/events", dependencies=A)
    def compat_events(start: str, end: str):
        s, e = _day(start, "start"), _day(end, "end")
        if e < s or (e - s).days > 366 * 2:
            raise HTTPException(400, "bad range")
        rev = {p["id"]: str(p.get("ecal_id") or p["id"]) for p in svc.profiles(False)}
        rows = [_compat_row(it, rev) for it in svc.instances(s, e)]
        return {"start": s.isoformat(), "end": e.isoformat(), "count": len(rows), "rows": rows}

    def _from_compat(body):
        m = _ecal_map()
        cats = body.get("userCalendarCategoryIds") or body.get("profiles") or []
        prof = [m[str(c).lower()] for c in cats if str(c).lower() in m and not m[str(c).lower()].startswith("cal:")]
        allday = bool(int(body.get("isAllDay") or 0))
        out = {"title": body.get("title")}
        if "description" in body:
            out["description"] = body.get("description") or ""
        if body.get("startDatetime"):
            if allday:
                out["start"] = body["startDatetime"][:10]
                out["end"] = (body.get("endDatetime") or body["startDatetime"])[:10]
            else:
                out["start"] = _utc_to_local(body["startDatetime"])
                out["end"] = _utc_to_local(body.get("endDatetime") or body["startDatetime"])
            out["all_day"] = allday
        rr = body.get("eventRecurrenceRule")
        if int(body.get("isRecurring") or 0) and isinstance(rr, dict):
            unit = {1: "DAILY", 2: "WEEKLY", 3: "MONTHLY", 4: "YEARLY"}.get(int(rr.get("recurrenceUnit") or 0))
            if unit:
                r = f"FREQ={unit};INTERVAL={int(rr.get('recurrenceValue') or 1)}"
                if unit == "WEEKLY" and rr.get("weekDays"):
                    r += ";BYDAY=" + ",".join(["SU", "MO", "TU", "WE", "TH", "FR", "SA"][int(d)] for d in rr["weekDays"])
                if rr.get("recurrenceEndDatetime"):
                    r += ";UNTIL=" + rr["recurrenceEndDatetime"][:10].replace("-", "") + "T235959"
                out["rrule"] = r
        if cats:
            out["profiles"] = prof
        return out

    @app.post("/events", dependencies=A)
    def compat_create(body: dict = Body(...)):
        if int(body.get("eventType") or 0) == 2:  # task -> chore
            m = _ecal_map()
            cats = [m.get(str(c).lower()) for c in body.get("userCalendarCategoryIds") or []]
            pid = next((c for c in cats if c and not c.startswith("cal:")), None)
            if not pid:
                raise HTTPException(400, "task needs a profile")
            ch = svc.add_chore({"title": body.get("title"), "profile_id": pid, "stars": body.get("starCount", 1),
                                "once_date": None if body.get("isRecurring") else (body.get("startDatetime") or "")[:10] or None})
            return {"code": 200, "msg": "success", "data": {"choreId": ch["id"]}}
        ev = _from_compat(body)
        if not ev.get("title") or not ev.get("start"):
            raise HTTPException(400, "title and startDatetime required")
        res = svc.create_event(ev, "shared", body.get("creator") or "routine")
        return {"code": 200, "msg": "success", "data": {"eventId": res["id"], "profiles": res["profiles"]}}

    @app.put("/events/{eid}", dependencies=A)
    def compat_update(eid: str, body: dict = Body(...)):
        ev = {k: v for k, v in _from_compat(body).items() if v is not None}
        res = nf(svc.update_event, eid, ev)
        return {"code": 200, "msg": "success", "data": {"eventId": res["id"]}}

    @app.delete("/events/{eid}", dependencies=A)
    def compat_delete(eid: str, series: bool = False):
        nf(svc.delete_event, eid)
        return {"code": 200, "msg": "success", "data": None}

    # ------------------------------------------------------------ background sync
    if start_sync:
        def loop():
            from .ecal_feed import Feed
            interval = float(os.environ.get("FAMCAL_SYNC_SECONDS", "60"))
            ecal_every = float(os.environ.get("FAMCAL_ECAL_SECONDS", "600"))
            feed, next_ecal = Feed(svc.db), 0.0
            svc.ecal_feed = feed
            svc.clear_past_auto_tags()
            while True:
                svc.sync()
                if time.time() >= next_ecal:
                    feed.run_once(); next_ecal = time.time() + ecal_every
                    svc.clear_past_auto_tags()
                else:
                    from .ecal_feed import dedupe
                    dedupe(svc.db)  # Google changes can create/remove duplicates
                time.sleep(interval)
        threading.Thread(target=loop, daemon=True, name="sync").start()
    return app
