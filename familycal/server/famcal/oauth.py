"""One-time Google sign-in (OAuth "Desktop app" client, loopback redirect + paste-back).

Flow (all from the phone web page, or famcal/google_setup.py in a terminal):
 1. POST /oauth/client {client_id, client_secret}   (from Google Cloud console, once)
 2. GET  /oauth/start -> {url}; open it, sign in as you@example.com, Allow.
 3. Browser lands on http://127.0.0.1:8765/?code=... (page won't load - that's expected);
    copy that address and POST /oauth/finish {url}.
 4. POST /oauth/calendars {shared: <calendarId>, soccer: <calendarId>} to pick calendars.
Secrets are written to the data volume with 0600 perms and never returned by the API.
"""
import json, os, secrets, urllib.parse, urllib.request
from fastapi import APIRouter, Body, HTTPException

SCOPE = "https://www.googleapis.com/auth/calendar"
REDIRECT = "http://127.0.0.1:8765/"


def _write_secret(path, obj):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)


def exchange(client, code, http=None):
    data = urllib.parse.urlencode({"code": code, "client_id": client["client_id"],
                                   "client_secret": client["client_secret"], "redirect_uri": REDIRECT,
                                   "grant_type": "authorization_code",
                                   "code_verifier": client.get("verifier", "")}).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def router(data_dir, deps, svc):
    r = APIRouter()
    cpath = os.path.join(data_dir, "google_client.json")
    tpath = os.path.join(data_dir, "google_token.json")

    def client():
        if not os.path.exists(cpath):
            raise HTTPException(400, "save the OAuth client first (POST /oauth/client)")
        with open(cpath) as f:
            return json.load(f)

    @r.get("/oauth/status", dependencies=deps)
    def status():
        return {"client_saved": os.path.exists(cpath), "signed_in": os.path.exists(tpath),
                "source": svc.source.name, "calendars": svc.calendars()}

    @r.post("/oauth/client", dependencies=deps)
    def save_client(body: dict = Body(...)):
        if not body.get("client_id") or not body.get("client_secret"):
            raise HTTPException(400, "client_id and client_secret required")
        _write_secret(cpath, {"client_id": body["client_id"].strip(), "client_secret": body["client_secret"].strip()})
        return {"ok": True}

    @r.get("/oauth/start", dependencies=deps)
    def start():
        import base64, hashlib
        c = client()
        c["verifier"] = secrets.token_urlsafe(48)
        c["state"] = secrets.token_urlsafe(12)
        _write_secret(cpath, c)
        chal = base64.urlsafe_b64encode(hashlib.sha256(c["verifier"].encode()).digest()).rstrip(b"=").decode()
        q = {"client_id": c["client_id"], "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
             "access_type": "offline", "prompt": "consent", "state": c["state"],
             "code_challenge": chal, "code_challenge_method": "S256"}
        return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q)}

    @r.post("/oauth/finish", dependencies=deps)
    def finish(body: dict = Body(...)):
        c = client()
        raw = (body.get("url") or body.get("code") or "").strip()
        if raw.startswith("http"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
            if qs.get("state", [None])[0] != c.get("state"):
                raise HTTPException(400, "state mismatch - start again")
            code = qs.get("code", [""])[0]
        else:
            code = raw
        if not code:
            raise HTTPException(400, "no code found")
        try:
            tok = exchange(c, code)
        except Exception as e:
            raise HTTPException(400, f"Google rejected the code ({type(e).__name__}); start again")
        if not tok.get("refresh_token"):
            raise HTTPException(400, "no refresh token returned; remove the app at myaccount.google.com/permissions and retry")
        _write_secret(tpath, {"client_id": c["client_id"], "client_secret": c["client_secret"],
                              "refresh_token": tok["refresh_token"]})
        from .sources import GoogleSource
        src = GoogleSource(tpath)
        cals = [{"id": x["id"], "name": x.get("summaryOverride") or x.get("summary"), "role": x.get("accessRole")}
                for x in src.list_calendars()]
        return {"ok": True, "calendars": cals}

    @r.post("/oauth/calendars", dependencies=deps)
    def pick(body: dict = Body(...)):
        """{shared: calendarId, soccer: calendarId} -> switch the service to Google and sync."""
        from .sources import GoogleSource, RoutingSource
        if not os.path.exists(tpath):
            raise HTTPException(400, "sign in first")
        for cid in ("shared", "soccer"):
            if body.get(cid):
                svc.db.x("UPDATE calendars SET google_id=? WHERE id=?", (body[cid], cid))
        svc.source = RoutingSource(GoogleSource(tpath))
        moved = 0
        if body.get("migrate_native", True):
            from .core import migrate_native
            moved = migrate_native(svc)
        n = svc.sync()
        return {"ok": n >= 0, "changed": n, "migrated_native_events": moved, "error": svc.last_sync_error}

    return r
