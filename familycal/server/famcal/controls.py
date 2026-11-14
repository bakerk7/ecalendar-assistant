"""Tablet controls for a few Home Assistant entities (fireplace switch, UniFi Protect alarm), proxied so the
tablet never holds the HA token.

Config `controls`: {"fireplace": {"entity": "switch.living_room_fireplace"},
                    "alarm": {"entity": "alarm_control_panel.baker_udmpro_alarm_manager", "arm_mode": "away"}}
Only these configured entities are ever read or written, and only with explicit on/off or arm/disarm services
(never a blind toggle), so a retried tap can't flip the state back. `dry_run: true` on a POST returns the HA
service call it would make without sending it (used for testing without lighting the fireplace or arming).
Env: HA_URL + HA_TOKEN (same as doors).
"""
import json, logging, os, threading, time, urllib.request

from . import config

log = logging.getLogger("famcal.controls")

ARM_SERVICES = {"away": "alarm_arm_away", "home": "alarm_arm_home", "night": "alarm_arm_night"}


class Controls:
    def __init__(self, ttl=1.5):
        self.ttl, self._cache, self._lock = ttl, (0.0, None), threading.Lock()

    def cfg(self):
        return config.get().get("controls") or {}

    def _ha(self, method, path, body=None):
        url, tok = os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "")
        if not url or not tok:
            raise RuntimeError("HA_URL/HA_TOKEN not set")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read() or b"null")

    def _read(self):
        out = {}
        for key, c in self.cfg().items():
            if key not in ("fireplace", "alarm") or not isinstance(c, dict):
                continue
            ent = (c or {}).get("entity")
            if not ent:
                continue
            row = {"state": None, "ok": True}
            try:
                st = self._ha("GET", f"/api/states/{ent}")
                row["state"] = st.get("state")
                row["ok"] = row["state"] not in (None, "unavailable", "unknown")
            except Exception as e:
                row["ok"] = False
                log.warning("HA read %s failed: %s", ent, type(e).__name__)
            if key == "fireplace":
                row["on"] = row["state"] == "on"
            elif key == "alarm":
                s = row["state"] or ""
                row["armed"] = s.startswith("armed") or s in ("arming", "triggered", "pending")
            out[key] = row
        return out

    def status(self, fresh=False):
        with self._lock:
            t, rows = self._cache
            if fresh or rows is None or time.time() - t > self.ttl:
                rows = self._read()
                self._cache = (time.time(), rows)
            return {k: dict(v) for k, v in rows.items()}

    def plan(self, key, want):
        """The HA service call for setting control `key` to `want` (bool): (domain, service, data)."""
        c = self.cfg().get(key) or {}
        ent = c.get("entity")
        if not ent:
            raise KeyError(key)
        if key == "fireplace":
            domain = ent.split(".", 1)[0]
            if domain not in ("switch", "light", "input_boolean"):
                raise ValueError(f"unsupported fireplace entity {ent}")
            return domain, "turn_on" if want else "turn_off", {"entity_id": ent}
        if key == "alarm":
            if not ent.startswith("alarm_control_panel."):
                raise ValueError(f"unsupported alarm entity {ent}")
            svc = ARM_SERVICES.get(c.get("arm_mode", "away"), "alarm_arm_away") if want else "alarm_disarm"
            return "alarm_control_panel", svc, {"entity_id": ent}
        raise KeyError(key)

    def set(self, key, want, dry_run=False):
        domain, svc, data = self.plan(key, want)
        call = {"domain": domain, "service": svc, "data": data}
        if dry_run:
            return {"dry_run": True, "call": call}
        log.info("HA call %s.%s %s", domain, svc, data["entity_id"])
        self._ha("POST", f"/api/services/{domain}/{svc}", data)
        with self._lock:
            self._cache = (0.0, None)
        return {"call": call, "controls": self.status(fresh=True)}
