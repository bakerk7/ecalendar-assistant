"""Read-only door-open alerts from Home Assistant, proxied so the tablet never holds the HA token.

Config `doors`: {"threshold_min": 5, "sensors": [{"name": "Front", "entity": "binary_sensor.front_door_contact"}]}.
Env: HA_URL (e.g. http://homeassistant:8123) and HA_TOKEN (long-lived token; only GET /api/states/<entity> is used).
A test override (POST /api/doors/fake) fakes open doors in memory without touching HA.
"""
import json, logging, os, threading, time, urllib.request
from datetime import datetime, timedelta, timezone

from . import config

log = logging.getLogger("famcal.doors")


class Doors:
    def __init__(self, ttl=20):
        self.ttl, self._cache, self._lock = ttl, (0.0, None), threading.Lock()
        self.fake = {}  # name -> opened_at (aware datetime)

    def cfg(self):
        return config.get().get("doors") or {}

    def _ha_state(self, entity):
        url, tok = os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "")
        if not url or not tok:
            return None
        req = urllib.request.Request(f"{url}/api/states/{entity}", headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read())

    def _read(self):
        out = []
        for s in self.cfg().get("sensors", []):
            row = {"name": s["name"], "entity": s["entity"], "open": False, "since": None, "ok": True}
            try:
                st = self._ha_state(s["entity"])
                if st is None:
                    row["ok"] = False
                else:
                    row["open"] = st.get("state") == "on"
                    row["since"] = st.get("last_changed")
            except Exception as e:
                row["ok"] = False
                log.warning("HA read %s failed: %s", s["entity"], type(e).__name__)
            out.append(row)
        return out

    def status(self):
        with self._lock:
            t, rows = self._cache
            if rows is None or time.time() - t > self.ttl:
                rows = self._read()
                self._cache = (time.time(), rows)
        now = datetime.now(timezone.utc)
        thr = int(self.cfg().get("threshold_min", 5))
        res = []
        for r in rows:
            r = dict(r)
            if r["name"] in self.fake:
                r.update(open=True, since=self.fake[r["name"]].isoformat(), ok=True, fake=True)
            mins = 0
            if r["open"] and r["since"]:
                try:
                    mins = int((now - datetime.fromisoformat(r["since"])).total_seconds() // 60)
                except ValueError:
                    pass
            r["open_min"] = mins
            r["alert"] = bool(r["open"] and mins >= thr)
            r.pop("entity", None)
            res.append(r)
        return {"threshold_min": thr, "doors": res, "alerts": [r for r in res if r["alert"]]}

    def set_fake(self, name, minutes):
        if minutes is None:
            self.fake.pop(name, None)
        else:
            self.fake[name] = datetime.now(timezone.utc) - timedelta(minutes=int(minutes))
