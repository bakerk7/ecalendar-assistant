"""Family-specific settings (profiles, colours, tagging rules, calendars).

Lookup order: $FAMCAL_CONFIG, $FAMCAL_DATA/config.json (e.g. /data/config.json in Docker),
../../config.local.json (dev checkout), then the bundled default_config.json (= config.example.json).
Profiles/calendars/rules only seed an empty database; aliases are read live.
"""
import json, os

_HERE = os.path.dirname(os.path.abspath(__file__))


def path():
    for p in (os.environ.get("FAMCAL_CONFIG"),
              os.path.join(os.environ.get("FAMCAL_DATA", "/data"), "config.json"),
              os.path.join(_HERE, "..", "..", "config.local.json"),
              os.path.join(_HERE, "default_config.json")):
        if p and os.path.isfile(p):
            return p


_cache = {}


def get():
    p = path()
    if p not in _cache:
        with open(p, encoding="utf-8") as f:
            _cache[p] = json.load(f)
    return _cache[p]


def family():
    f = dict(get().get("family") or {})
    return {"id": "family", "name": f.get("name", "Family"), "color": f.get("color", "#6B7280"),
            "emoji": f.get("emoji", "🏠"), "kind": "group", "sort": 99}


def profiles():
    """[(id, name, color, emoji, sort)] in configured order."""
    return [(p["id"], p["name"], p.get("color", "#888888"), p.get("emoji", ""), i + 1)
            for i, p in enumerate(get().get("profiles", []))]


def aliases():
    return {p["id"]: [a.lower() for a in (p.get("aliases") or [p["name"]])] for p in get().get("profiles", [])}


def calendars():
    return get().get("calendars", [])


def rules():
    return [(r["kind"], r.get("pattern", ""), r.get("profiles", []), r.get("priority", 50), r.get("note", ""))
            for r in get().get("rules", [])]


def importer():
    return get().get("importer", {})
