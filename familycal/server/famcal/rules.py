"""Auto-tagging: decide which family profiles an event belongs to.

Order: explicit tags already on the Google event (extendedProperties) > manual tag history
for the same title > rules by priority (calendar, keyword, creator) > default Family (needs review).
"""
import json, re

DEFAULT_RULES = [
    # kind, pattern, profiles, priority, note
    ("calendar", "soccer", ["jack"], 10, "Boys U9 Academy 2 White feed is Jack's"),
    ("keyword", r"\b(soccer|u9|academy)\b", ["jack"], 50, "soccer -> Jack"),
    ("keyword", r"\bswim(ming)?\b", ["jack", "liv"], 60, "swim -> Jack + Liv"),
    ("keyword", r"\b(vet|groom(er|ing)?|dog park)\b", ["bruce"], 60, "dog stuff -> Bruce"),
    ("name", "", [], 80, "names in title (Jack, Liv, Mom/Sara, Dad/Kyle, Bruce)"),
]

NAME_ALIASES = {"jack": ["jack"], "liv": ["liv", "livvy", "olivia"], "mom": ["mom", "sara"],
                "dad": ["dad", "kyle"], "bruce": ["bruce"]}


def seed_rules(db):
    if db.one("SELECT COUNT(*) n FROM rules")["n"]:
        return
    db.xm("INSERT INTO rules(kind,pattern,profiles,priority,note) VALUES(?,?,?,?,?)",
          [(k, p, json.dumps(pr), pri, n) for k, p, pr, pri, n in DEFAULT_RULES])


def norm_title(t):
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def names_in(title, valid):
    t = (title or "").lower()
    out = []
    for pid, aliases in NAME_ALIASES.items():
        if pid in valid and any(re.search(r"\b%s('s)?\b" % re.escape(a), t) for a in aliases):
            out.append(pid)
    return out


def decide(db, ev, cal, valid_profiles, creator_defaults=None):
    """Return (profiles, source, rule_note, needs_review)."""
    if ev.get("tags") is not None:
        return [p for p in ev["tags"] if p in valid_profiles], "google", "extendedProperties", 0
    # same title manually tagged before -> reuse
    nt = norm_title(ev.get("title"))
    if nt:
        hist = db.q("""SELECT e.title, t.profiles FROM tags t JOIN events e ON e.id=t.event_id
                       WHERE t.source='manual' ORDER BY t.updated DESC LIMIT 400""")
        for h in hist:
            if norm_title(h["title"]) == nt:
                return json.loads(h["profiles"]), "rule", "same title tagged before", 0
    for r in db.q("SELECT * FROM rules ORDER BY priority, id"):
        prof = [p for p in json.loads(r["profiles"]) if p in valid_profiles]
        if r["kind"] == "calendar" and cal == r["pattern"] and prof:
            return prof, "rule", r["note"] or "calendar", 0
        if r["kind"] == "keyword":
            try:
                if re.search(r["pattern"], ev.get("title") or "", re.I) and prof:
                    return prof, "rule", r["note"] or r["pattern"], 0
            except re.error:
                continue
        if r["kind"] == "name":
            hits = names_in(ev.get("title"), valid_profiles)
            if hits:
                return hits, "rule", "name in title", 0
        if r["kind"] == "creator" and ev.get("creator") and r["pattern"].lower() == ev["creator"].lower() and prof:
            return prof, "rule", r["note"] or "creator default", 0
    return [], "default", "untagged -> Family", 1
