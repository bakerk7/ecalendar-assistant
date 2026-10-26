"""Helper for talking to eCalendar's private API (api.cd.myecalendar.com).

Mirrors real requests captured from the eCalendar macOS/iOS app.

This is the GENERIC template: every account-specific value comes from an environment
variable. See `SETUP.md` for how to find your own values and where to put them.
`ecalendar-api.md` is the full endpoint reference behind this helper.

Auth token resolution order:
  1. $ECALENDAR_TOKEN               <- use this in cloud / Claude Code web
  2. the eCalendar macOS app plist  <- automatic if running on the same Mac you're
                                        signed into the app on

The token has no time-based expiry, but any new sign-in to the account (on any
device) issues a new one and kills the old: calls then come back as
{"code": 401, "msg": "Account logged in on another device"}, which api() raises as
TokenReplacedError. It is a full-account bearer credential (read+write every note,
event, task on the account) -- keep it in a secret store, never in a repo.
"""
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import warnings
from datetime import date, datetime, timedelta, timezone

BASE = "https://api.cd.myecalendar.com"
PLIST = os.path.expanduser(
    "~/Library/Containers/com.fujia.ecalendar/Data/Library/Preferences/com.fujia.ecalendar.plist")


# calendar category (a.k.a. profile) ids -- set ECALENDAR_CATEGORIES to a JSON object,
# e.g. {"family": "123...", "kid_a": "456...", "kid_b": "789..."}. See SETUP.md.
# meal category ids (Breakfast/Lunch/Dinner/Snack, as configured in the app's Meals
# tab) -- set ECALENDAR_MEAL_CATEGORIES to a JSON object, e.g.
# {"breakfast": "123...", "lunch": "456..."}. See SETUP.md.

def _json_object_env(name):
    """Parse an env var as a JSON object. Empty/unset -> {}.

    A bad value used to raise a bare JSONDecodeError at import. Raise a setup
    error that names the variable instead.
    """
    raw = os.environ.get(name, "")
    if raw is None or not str(raw).strip():
        return {}
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"{name} is not valid JSON ({e.msg}). Expected an object like "
            '{"family": "123"}. See SETUP.md. Do not source the auth file from '
            "bash — quote removal mangles the JSON."
        ) from e
    if not isinstance(val, dict):
        raise RuntimeError(
            f"{name} must be a JSON object of name -> id, got {type(val).__name__}."
        )
    return val


def _default_category_key(categories):
    """Key used when category= is omitted.

    ECALENDAR_DEFAULT_CATEGORY if set; otherwise `family`, then `home`, then the
    first key. Matches README / extract_ecal_config.py (a key, not a raw id).
    """
    explicit = (os.environ.get("ECALENDAR_DEFAULT_CATEGORY") or "").strip()
    if explicit:
        return explicit
    by_lower = {str(k).lower(): k for k in categories}
    for name in ("family", "home"):
        if name in by_lower:
            return by_lower[name]
    return next(iter(categories), None)


def _refresh_from_env():
    """(Re)read module config from the environment. Runs once at import; also
    called by load_auth_file() so `import ecal; ecal.load_auth_file()` works."""
    global DEVICE, INSTANCE, TIMEZONE, CATEGORIES, DEFAULT_CATEGORY, MEAL_CATEGORIES
    DEVICE = os.environ.get("ECALENDAR_DEVICE", "")
    INSTANCE = os.environ.get("ECALENDAR_INSTANCE", "")  # the app's clientInstanceId (Notes only)
    # IANA zone your account is actually in -- NOT necessarily US-Eastern. The app sends
    # this as a header and as a numeric UTC offset with every write; get it wrong and
    # events/meals can land displayed an hour off. See SETUP.md for how to check yours
    # (hint: the addZone/zone value in any of your own captured requests tells you).
    TIMEZONE = os.environ.get("ECALENDAR_TIMEZONE", "America/New_York")
    CATEGORIES = _json_object_env("ECALENDAR_CATEGORIES")
    DEFAULT_CATEGORY = _default_category_key(CATEGORIES)
    MEAL_CATEGORIES = _json_object_env("ECALENDAR_MEAL_CATEGORIES")

_refresh_from_env()

# recurrenceUnit values for eventRecurrenceRule
DAILY, WEEKLY, MONTHLY, YEARLY = 1, 2, 3, 4


def _require_setup(need_instance=False):
    checks = [("ECALENDAR_DEVICE", DEVICE), ("ECALENDAR_CATEGORIES", CATEGORIES or None)]
    if need_instance:
        checks.append(("ECALENDAR_INSTANCE", INSTANCE))
    missing = [n for n, v in checks if not v]
    if missing:
        raise RuntimeError(f"missing setup: {', '.join(missing)} -- see SETUP.md")


def _require_device():
    if not DEVICE:
        raise RuntimeError("missing setup: ECALENDAR_DEVICE -- see SETUP.md")


def token():
    env = os.environ.get("ECALENDAR_TOKEN")
    if env:
        return env.strip()
    import plistlib
    with open(PLIST, "rb") as f:
        d = plistlib.load(f)
    return json.loads(d["flutter.user"])["token"]


def _warn_auth_perms(path):
    """The auth file is a full-account credential. Warn if group/other can read it."""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if mode & 0o077:
        warnings.warn(
            f"{path} is readable by other users (mode {mode & 0o777:o}). "
            "It holds a full-account token — chmod 600 it.",
            UserWarning,
            stacklevel=3,
        )


def load_auth_file(path="./ECALENDAR_INFO.bin"):
    """Parse a KEY=value auth file into os.environ and refresh module config,
    so `import ecal; ecal.load_auth_file()` just works. Pass the path to your
    own file if it lives elsewhere.

    Handles the backslash-escaped JSON in ECALENDAR_CATEGORIES -- do NOT
    `source` this file with bash, bash quote-removal mangles it.
    Warns if the file is readable by group or other (it should be mode 0600).
    """
    p = os.path.expanduser(path)
    _warn_auth_perms(p)
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            if k == "ECALENDAR_CATEGORIES":
                v = v.replace('\\"', '"')
            os.environ[k] = v
    _refresh_from_env()


# standard/daylight UTC-offset pairs for the zoneinfo-less fallback below. Add yours
# if it's not here and you're on a platform without a system tz database (rare --
# zoneinfo works out of the box on macOS/Linux; Windows may need `pip install tzdata`).
_US_TZ_OFFSETS = {
    "America/New_York": (-5, -4), "America/Chicago": (-6, -5),
    "America/Denver": (-7, -6), "America/Los_Angeles": (-8, -7),
}


def _as_date(on):
    if on is None:
        return date.today()
    if isinstance(on, datetime):
        return on.date()
    if isinstance(on, date):
        return on
    return datetime.strptime(str(on)[:10], "%Y-%m-%d").date()


def _hours_from_delta(delta):
    """Whole hours as int (-5); half-hour and 45-minute zones keep the fraction (5.5, 5.75)."""
    hours = round(delta.total_seconds() / 3600.0, 2)
    if hours == int(hours):
        return int(hours)
    return hours


def _try_zoneinfo():
    """ZoneInfo for TIMEZONE, or None if zoneinfo/tzdata can't resolve it."""
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError:
        return None
    try:
        return ZoneInfo(TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _us_fallback_offset(d):
    """US DST rule (2nd Sunday of March .. 1st Sunday of November), date granularity.

    Only for zones in _US_TZ_OFFSETS, and only when zoneinfo itself can't load.
    Noon on the transition Sunday is already the post-transition offset, which
    matches `start <= d < end`.
    """
    def nth_sun(y, m, n):
        first = date(y, m, 1)
        first_sun = first + timedelta((6 - first.weekday()) % 7)
        return first_sun + timedelta(7 * (n - 1))
    start, end = nth_sun(d.year, 3, 2), nth_sun(d.year, 11, 1)
    std, dst = _US_TZ_OFFSETS[TIMEZONE]
    return dst if start <= d < end else std


def tz_offset(on=None):
    """UTC offset in hours for ECALENDAR_TIMEZONE (DST-aware) on a given date.

    Whole-hour zones return an int. Half-hour and 45-minute zones return a float
    (5.5, -3.5, 5.75) so the offset isn't truncated — and so a negative half-hour
    zone isn't rounded the wrong way by floor division.

    If zoneinfo can't resolve TIMEZONE, known US zones fall back to a fixed DST
    rule. Anything else raises. This does not assume America/New_York.
    """
    d = _as_date(on)
    zi = _try_zoneinfo()
    if zi is not None:
        off = datetime(d.year, d.month, d.day, 12, tzinfo=zi).utcoffset()
        if off is None:
            raise RuntimeError(f"timezone {TIMEZONE!r} has no UTC offset on {d.isoformat()}")
        return _hours_from_delta(off)
    if TIMEZONE in _US_TZ_OFFSETS:
        return _us_fallback_offset(d)
    raise RuntimeError(
        f"cannot resolve timezone {TIMEZONE!r}. Install tzdata (`pip install tzdata`) "
        "or set ECALENDAR_TIMEZONE to an IANA zone this system knows. "
        "Refusing to assume US Eastern."
    )


def _parse_clock(text):
    """Parse 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD HH:MM' (T also accepted) to a naive datetime."""
    text = (text or "").strip().replace("T", " ")
    if len(text) >= 19:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    if len(text) >= 16:
        return datetime.strptime(text[:16], "%Y-%m-%d %H:%M")
    raise ValueError(f"expected a datetime, got {text!r}")


def _local_to_utc(naive_local):
    """Naive local wall time in ECALENDAR_TIMEZONE -> naive UTC."""
    zi = _try_zoneinfo()
    if zi is not None:
        return naive_local.replace(tzinfo=zi).astimezone(timezone.utc).replace(tzinfo=None)
    return naive_local - timedelta(hours=float(tz_offset(naive_local.date())))


def _utc_to_local(naive_utc):
    """Naive UTC -> naive local wall time in ECALENDAR_TIMEZONE."""
    zi = _try_zoneinfo()
    if zi is not None:
        return naive_utc.replace(tzinfo=timezone.utc).astimezone(zi).replace(tzinfo=None)
    return naive_utc + timedelta(hours=float(tz_offset(naive_utc.date())))


def _local_str_to_utc_str(text):
    return _local_to_utc(_parse_clock(text)).strftime("%Y-%m-%d %H:%M:%S")


def _is_all_day(row):
    v = row.get("isAllDay", row.get("allDay"))
    return v in (1, True, "1")


def event_local_start(row):
    """Naive local start of a list row, or None if `startDatetime` doesn't parse.

    Timed rows: `startDatetime` is UTC (verified 2026-09-13 for create/edit) and is
    converted to ECALENDAR_TIMEZONE.
    All-day rows: `startDatetime` is treated as a calendar-date marker, not a UTC
    instant. See the note on `_event_body` — this needs a device check.
    """
    raw = row.get("startDatetime") or ""
    try:
        dt = _parse_clock(raw)
    except ValueError:
        return None
    if _is_all_day(row):
        return dt
    return _utc_to_local(dt)


def _headers(tok):
    return {
        "user-agent": "Dart/3.9 (dart:io)", "key": tok, "authorization": "Bearer " + tok,
        "x-time-zone": TIMEZONE, "x-zone-id": TIMEZONE,
        "x-client-capabilities": "routine-v1", "resource": "app", "x-language": "en",
        "versionname": "571", "content-type": "application/json",
    }


class TokenReplacedError(RuntimeError):
    """The token was invalidated -- almost always because someone signed in to the
    account again (any device), which issues a new token and kills the old one."""


# One extra attempt, after this pause, for idempotent calls that time out or get HTTP 5xx.
_RETRY_BACKOFF = 0.5


def _check_auth(path, r):
    # Auth failures come back as HTTP 200 with code 401 in the body, e.g.
    #   {"code": 401, "msg": "Account logged in on another device", "data": null}
    if isinstance(r, dict) and r.get("code") == 401:
        raise TokenReplacedError(
            f"{path} -> code=401 msg={r.get('msg')}. The token is no longer valid -- a newer "
            "sign-in replaced it. Get the current one (python3 extract_ecal_config.py "
            "on a Mac signed into eCalendar) and update ECALENDAR_TOKEN.")
    return r


def _check_code(path, r):
    """Shared success check for every API call.

    The server returns HTTP 200 with a JSON `code` for both success and failure.
    Callers used to check this ad hoc, and several (delete, note sync, presign,
    find_recipe) never did. Anything other than 200 raises. The message carries
    `code` and `msg` only — never the rest of the payload.
    """
    if not isinstance(r, dict):
        raise RuntimeError(f"{path} -> code=None msg=response was not a JSON object")
    code = r.get("code")
    if code != 200:
        raise RuntimeError(f"{path} -> code={code} msg={r.get('msg')}")
    return r


def _http_error(path, exc):
    """RuntimeError from an HTTPError, using JSON code/msg when the body has them."""
    body = ""
    try:
        body = exc.read().decode(errors="replace")
    except Exception:
        body = ""
    code, msg = exc.code, None
    try:
        parsed = json.loads(body) if body else None
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        code = parsed.get("code", exc.code)
        msg = parsed.get("msg")
    if not msg:
        msg = getattr(exc, "reason", None) or "HTTP error"
    return RuntimeError(f"{path} -> code={code} msg={msg}")


def _is_timeout(exc):
    # socket.timeout is TimeoutError on 3.10+ and a distinct OSError on 3.9.
    timeout_types = (TimeoutError, socket.timeout)
    if isinstance(exc, timeout_types):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, timeout_types)


def _decode_response(path, raw):
    if _looks_gzipped(raw):
        import gzip
        raw = gzip.decompress(raw)
    return _check_code(path, _check_auth(path, json.loads(raw.decode())))


def api(path, body, tok=None, method="POST"):
    """Call an endpoint. POST sends `body` as JSON; method="GET" ignores `body`
    (put query parameters in `path`).

    Raises TokenReplacedError on code 401, and RuntimeError when `code` is not 200.
    Idempotent calls (see `_retry_safe`) are retried once, after `_RETRY_BACKOFF`
    seconds, on timeout or HTTP 5xx. A truncated body is re-read via curl instead
    (creates are never re-fired).
    """
    tok = tok or token()
    data = json.dumps(body).encode() if method == "POST" else None
    headers = _headers(tok)
    attempts = 2 if _retry_safe(path) else 1
    for attempt in range(attempts):
        if attempt:
            time.sleep(_RETRY_BACKOFF)
        try:
            raw = _urlopen_read(path, data, headers, method)
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt + 1 < attempts:
                continue
            raise _http_error(path, e) from e
        except urllib.error.URLError as e:
            if _is_timeout(e) and attempt + 1 < attempts:
                continue
            if _is_timeout(e):
                raise TimeoutError(f"{path} timed out") from e
            raise
        except (TimeoutError, socket.timeout):
            if attempt + 1 < attempts:
                continue
            raise
        return _decode_response(path, raw)
    raise RuntimeError(f"{path} -> code=None msg=request failed")


def _urlopen_read(path, data, headers, method):
    """Read a response body. Truncated chunked bodies fall back to curl.

    Creates are not re-fired: a create that raised may still have landed
    server-side. Callers must verify with find_duplicate() before retrying.
    Timeouts propagate so api() can apply the single backoff retry.
    """
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.read()
    except (http.client.IncompleteRead, http.client.RemoteDisconnected,
            ConnectionResetError):
        if not _retry_safe(path):
            raise
        return _curl_post(path, data, headers)


# creates whose path doesn't contain "/add"
_CREATE_PATHS = {"/app/task", "/app/v2/recipe", "/app/mealplan"}


def _retry_safe(path):
    """Reads, edits and deletes are idempotent -- safe to re-fire through curl
    when urllib chokes on a truncated response. Creates (/add, plus the bare
    create paths above) are not: a failed create may still have landed
    server-side."""
    p = path.lower().split("?")[0].rstrip("/")
    return "/add" not in p and p not in _CREATE_PATHS


def _looks_gzipped(raw):
    return raw[:2] == b"\x1f\x8b"


def _curl_post(path, data, headers, timeout=60):
    """POST via curl (temp files keep the token out of argv), or GET when `data`
    is None. Fallback for idempotent calls when urllib chokes on a truncated
    chunked response."""
    hf = tempfile.NamedTemporaryFile("w", delete=False, suffix=".hdr")
    bf = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".json")
    try:
        hf.write("\n".join(f"{k}: {v}" for k, v in headers.items()))
        hf.close()
        bf.write(data or b"")
        bf.close()
        body_args = ["-X", "POST", "--data", "@" + bf.name] if data is not None else ["-X", "GET"]
        p = subprocess.run(
            ["curl", "-sS", "--fail-with-body", "--max-time", str(timeout), *body_args,
             BASE + path, "-H", "@" + hf.name],
            capture_output=True, timeout=timeout + 30)
        if p.returncode != 0:
            code, msg = p.returncode, None
            try:
                parsed = json.loads(p.stdout.decode(errors="replace")) if p.stdout else None
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                code = parsed.get("code", p.returncode)
                msg = parsed.get("msg")
            if not msg:
                msg = "curl request failed"
            raise RuntimeError(f"curl fallback for {path} failed: code={code} msg={msg}")
        return p.stdout
    finally:
        for f in (hf.name, bf.name):
            try:
                os.unlink(f)
            except OSError:
                pass


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _lookup_id(mapping, name):
    """Case-insensitive key lookup. Unknown names pass through as raw ids."""
    key = str(name).strip().lower()
    for k, v in mapping.items():
        if str(k).lower() == key:
            return v
    return name


def resolve_category(name):
    """Map a short key ('family', 'kid_a') to its category id. `name` may already be
    an id (passed through). Falls back to ECALENDAR_DEFAULT_CATEGORY.
    Keys match case-insensitively."""
    name = name or DEFAULT_CATEGORY
    if not name:
        return None
    if isinstance(name, (list, tuple)):
        return [resolve_category(n) for n in name]
    return _lookup_id(CATEGORIES, name)


# ================================================================ events

def list_events(start_date, end_date, tok=None):
    """start_date / end_date = 'YYYY-MM-DD' (inclusive).

    Returns rows whose **local** start date falls in [start_date-1d, end_date+1d].
    Timed rows store `startDatetime` as UTC; that instant is converted to
    ECALENDAR_TIMEZONE before the comparison. All-day rows are compared as
    calendar dates (see `event_local_start`). The API rejects ranges < 7 days,
    so this always fetches a widened window and filters client-side.
    """
    _require_setup()
    s = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
    e = datetime.strptime(end_date[:10], "%Y-%m-%d").date()
    fetch_s = s - timedelta(days=3)
    fetch_e = max(e + timedelta(days=1), fetch_s + timedelta(days=9))
    r = api("/app/event/list", {"deviceId": str(DEVICE),
            "startDatetime": f"{fetch_s.isoformat()} 00:00:00",
            "endDatetime": f"{fetch_e.isoformat()} 23:59:59"}, tok)
    lo, hi = s - timedelta(days=1), e + timedelta(days=1)
    out = []
    for row in r.get("rows", []):
        local = event_local_start(row)
        if local is None:
            out.append(row)
            continue
        if lo <= local.date() <= hi:
            out.append(row)
    return out


def events_on(day, tok=None):
    """Convenience: event rows whose local start is within +/-1 day of 'YYYY-MM-DD'."""
    return list_events(day[:10], day[:10], tok)


_TITLE_WORD = re.compile(r"[a-z0-9]+")
# |intersection| / max(|a|, |b|). Shared with ecal_cache so the two dedupers agree.
TITLE_MATCH = 0.6
# Timed dedupe: same local clock within this window. Far enough apart that a
# weekly series' other hour (or a same-day second showing) is a different event.
DUPLICATE_TIME_TOLERANCE = timedelta(minutes=15)


def title_words(text):
    """Significant words (length > 2) used by the title scorer."""
    return {w for w in _TITLE_WORD.findall((text or "").lower()) if len(w) > 2}


def title_score(a, b):
    """Significant-word overlap in [0, 1].

    `|intersection| / max(|a|, |b|)`. A one-word query does not match a much
    longer different title — the old ecal.py formula (divide by the query size
    only) and the cache formula disagreed on that case. Empty on either side is 0.
    """
    want, have = title_words(a), title_words(b)
    if not want or not have:
        return 0.0
    return len(want & have) / max(len(want), len(have))


def _parse_when(day, at=None):
    """(local date, local time or None).

    `day` is 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM[:SS]'. `at` overrides the clock
    ('HH:MM[:SS]' or a full datetime).
    """
    text = (day or "").strip().replace("T", " ")
    target_date = datetime.strptime(text[:10], "%Y-%m-%d").date()
    target_time = None
    if len(text) >= 16 and text[10] == " ":
        target_time = _parse_clock(text).time().replace(microsecond=0)
    if at:
        at_text = str(at).strip().replace("T", " ")
        if len(at_text) <= 8:
            fmt = "%H:%M:%S" if at_text.count(":") == 2 else "%H:%M"
            target_time = datetime.strptime(at_text, fmt).time()
        else:
            parsed = _parse_clock(at_text)
            target_date = parsed.date()
            target_time = parsed.time().replace(microsecond=0)
    return target_date, target_time


def pick_duplicate(rows, title, day, at=None):
    """Best matching row in `rows`, or None. See `find_duplicate`."""
    target_date, target_time = _parse_when(day, at)
    best, best_score = None, 0.0
    for row in rows:
        score = title_score(title, row.get("title"))
        if score < TITLE_MATCH or score <= best_score:
            continue
        local = event_local_start(row)
        if local is None:
            continue
        if target_time is not None and not _is_all_day(row):
            # Same local calendar day and a close clock time. A weekly series'
            # other instance (different day, or the same title at another hour)
            # must not win.
            if local.date() != target_date:
                continue
            target_dt = datetime.combine(target_date, target_time)
            if abs(local - target_dt) > DUPLICATE_TIME_TOLERANCE:
                continue
        elif abs((local.date() - target_date).days) > 1:
            continue
        best, best_score = row, score
    return best


def find_duplicate(title, day, tok=None, *, at=None):
    """An existing event row that looks like the same thing, else None.

    Title match is `title_score` >= 0.6 (the best row wins).

    `day` is 'YYYY-MM-DD' or a local 'YYYY-MM-DD HH:MM:SS'. `at=` is an optional
    clock time. When a time is given, a timed row matches only on that local date
    and within 15 minutes — so creating "Piano" at 4pm does not collide with the
    6pm instance, or with last week's. With no time (all-day, or the caller only
    knows the date), match the title within +/-1 local day.

    Dates are compared in local time, not the UTC date prefix of `startDatetime`.
    """
    target_date, _ = _parse_when(day, at)
    return pick_duplicate(events_on(target_date.isoformat(), tok), title, day, at=at)


def recurrence(unit, value=1, week_days=None, end=None, month_option=None):
    """Build an eventRecurrenceRule. unit = DAILY/WEEKLY/MONTHLY/YEARLY.
    week_days (WEEKLY only) = list of ints 0=Sun..6=Sat. end = 'YYYY-MM-DD' or None."""
    return {
        "eventRecurrenceRulesId": None,
        "recurrenceValue": value,
        "recurrenceUnit": unit,
        "weekDays": week_days,
        "recurrenceMonthOption": month_option,
        "recurrenceEndDatetime": f"{end} 23:59:59" if end and len(end) == 10 else end,
    }


def _event_body(title, start, end, *, all_day, description, category, location,
                reminders_min, recur, add_calendar_id):
    """Build an event/add body.

    Timed start/end are local wall times. They are converted to UTC here because
    the API stores those instants as UTC (verified 2026-09-13: sending wall time
    makes the event show hours early). `zone` stays the local date's UTC offset.

    All-day events are NOT converted. They are sent as the calendar date
    `00:00:00`–`23:59:59` plus `zone`. Assumption (needs a device check): the app
    treats that pair as a date marker, not as a UTC instant. Shifting it to UTC
    would move the end onto the next calendar day. Tasks (`23:59:59` on the due
    date) and meals (`mealDateTime` = that date `23:59:59` + `addZone`) follow
    the same assumption and are left in local form by their own builders.
    """
    cats = resolve_category(category)
    if isinstance(cats, str):
        cats = [cats]
    if all_day:
        d = start[:10]
        sdt, edt = f"{d} 00:00:00", f"{d} 23:59:59"
        reminders = [{"method": "popup", "timeUnit": "minute", "value": 540}]
        zone_ref = d
    else:
        if end is None:
            st = _parse_clock(start)
            end = (st + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        # +1h is applied in local time, then both ends are converted, so a DST
        # boundary doesn't change the requested wall-clock duration.
        sdt, edt = _local_str_to_utc_str(start), _local_str_to_utc_str(end)
        reminders = [{"method": "popup", "timeUnit": "minute", "value": int(m)}
                     for m in (reminders_min or ())]
        zone_ref = start
    return {
        "deviceId": str(DEVICE), "title": title, "description": description,
        "eventType": 0, "isAllDay": 1 if all_day else 0,
        "startDatetime": sdt, "endDatetime": edt,
        "isRecurring": 1 if recur else 0, "eventRecurrenceRule": recur,
        "locationInfo": location, "weatherInfo": None, "currentCity": None,
        "googleCalendarId": None, "addCalendarId": add_calendar_id,
        "userCalendarCategoryIds": cats, "zone": tz_offset(zone_ref),
        "apiVersion": "2.0", "isAnniversary": 0, "isAnniversaryPinned": 0,
        "reminderPipelineVersion": 1, "timeReminders": reminders,
    }


def create_event(title, start, end=None, *, all_day=False, description="",
                 category=None, location=None, reminders_min=(0,),
                 recur=None, add_calendar_id=None, tok=None):
    """Create a calendar event.

    all_day=True  : `start` = 'YYYY-MM-DD'. Gets a 9:00 AM popup (all-day events only
                    accept one minute-based reminder). Sent as that date
                    00:00:00–23:59:59 (not converted to UTC — see `_event_body`).
    all_day=False : `start`/`end` = 'YYYY-MM-DD HH:MM:SS' **local wall time** in
                    ECALENDAR_TIMEZONE. This converts them to UTC internally (the
                    API stores UTC; sending wall time on the wire makes events show
                    hours early, verified 2026-09-13). Do not pre-convert. `end`
                    defaults to start + 1h local. reminders_min = minutes-before
                    popups. `dateDescription` on the row is the reliable
                    "what the app shows" check.
    category      : a key from ECALENDAR_CATEGORIES, or a raw id. Defaults to
                    ECALENDAR_DEFAULT_CATEGORY.
    recur         : an eventRecurrenceRule dict (see recurrence()), or None.

    Returns {"code":200,"data":null} -- NO id. Dedupe with find_duplicate() first.
    """
    _require_setup()
    body = _event_body(title, start, end, all_day=all_day, description=description,
                       category=category, location=location, reminders_min=reminders_min,
                       recur=recur, add_calendar_id=add_calendar_id)
    return api("/app/event/add", body, tok)


def create_events(specs, max_workers=4):
    """Create many events in parallel (the API has no batch endpoint).

    specs: a list of dicts, each one matching create_event's kwargs, e.g.
        {"title": "Dentist", "start": "2026-09-20 14:00:00", "category": "family"}

    Runs up to max_workers creates concurrently and returns a results list in
    input order: [(True, None), (False, "RemoteDisconnected: ..."), ...].
    Note: a create that raises a network error may still have landed
    server-side -- verify with find_duplicate() before retrying failures.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _one(spec):
        try:
            create_event(**spec)
            return (True, None)
        except Exception as e:
            return (False, f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(_one, specs))


def edit_event(event_id, title, start, end=None, *, all_day=False, description="",
               category=None, location=None, reminders_min=(0,), recur=None,
               update_method=None, origin_event_id=None, origin_start=None, origin_end=None,
               tok=None):
    """Update an event. `start`/`end` are local wall time, same as create_event.

    For a single recurring instance pass update_method (0=this / 1=all / 2=this+future)
    + origin_event_id + origin_start + origin_end from the row. Pass origin_* exactly
    as listed (UTC on the wire) — they are not converted again.
    """
    _require_setup()
    body = _event_body(title, start, end, all_day=all_day, description=description,
                       category=category, location=location, reminders_min=reminders_min,
                       recur=recur, add_calendar_id=None)
    body.pop("addCalendarId", None)
    body.update({
        "eventId": str(event_id), "updateMethod": update_method,
        "originEventId": origin_event_id, "originStartDatetime": origin_start,
        "originEndDatetime": origin_end,
    })
    return api("/app/event/edit/selective", body, tok)


def delete_event(event_id, *, series=False, origin_event_id=None,
                 origin_start=None, origin_end=None, tok=None):
    """Delete an event.

    default     : deleteMethod 0 -- a non-recurring event, or a single instance (for a
                  non-root instance also pass origin_event_id/origin_start/origin_end).
    series=True : deleteMethod 2 -- the whole recurring series. Pass the series root
                  eventId (row's eventRecurrenceRule.eventRecurrenceRulesId or
                  routineSourceEventId, falling back to eventId).
    """
    _require_setup()
    body = {
        "eventId": str(event_id),
        "deleteMethod": 2 if series else 0,
        "originEventId": origin_event_id,
        "originStartDatetime": origin_start,
        "originEndDatetime": origin_end,
    }
    return api("/app/event/delete/selective", body, tok)


def create_anniversary(title, day, *, category=None, description="", pinned=False, tok=None):
    """An anniversary = an all-day, yearly-recurring event with isAnniversary:1.
    `day` = 'YYYY-MM-DD' (the original date)."""
    _require_setup()
    body = _event_body(title, day, None, all_day=True, description=description,
                       category=category, location=None, reminders_min=(),
                       recur=recurrence(YEARLY), add_calendar_id=None)
    body.update({"isAnniversary": 1, "isAnniversaryPinned": 1 if pinned else 0})
    return api("/app/event/add", body, tok)


# ================================================================ tasks / chores

def create_task(title, *, category, due=None, stars=0, emoji="MEMO", description="",
                recur=None, timer_seconds=None, tok=None):
    """Create a Task (the app's chore / to-do item, in the Tasks tab, assigned to a
    person, with an optional star reward).

    category     : who it's for -- a key from ECALENDAR_CATEGORIES or a raw id.
    due          : 'YYYY-MM-DD' the task is due (all-day). Defaults to today.
    stars        : reward stars on completion. Defaults to 0 (no reward) -- only
                   set it when the user asks for stars.
    emoji        : an emoji *name* (e.g. "BROOM", "BOWL WITH SPOON"). The API also
                   accepts null (no icon) but rejects "".
    recur        : an eventRecurrenceRule (see recurrence()) for a repeating chore.
    timer_seconds: optional focus-timer length.

    For a plain one-off *deadline* (not a chore assigned to someone) use
    create_event(all_day=True) instead.
    """
    _require_setup()
    cat = resolve_category(category)
    if isinstance(cat, list):
        cat = cat[0]
    d = (due or date.today().isoformat())[:10]
    body = {
        "deviceId": str(DEVICE), "title": title, "description": description,
        "eventType": "2", "taskType": 0,
        "isAllDay": 1, "startDatetime": f"{d} 23:59:59",
        "userCalendarCategoryIds": [cat], "zone": tz_offset(d),
        "emoji": emoji or "MEMO", "starCount": str(max(0, int(stars))), "priority": "0",
        "timerDurationSeconds": timer_seconds,
        "taskTimeoutPenalty": 0, "taskTimeoutPenaltyStarPercent": "0.00",
        "taskTimeoutPenaltyStarCount": 0,
        "isRecurring": 1 if recur else 0, "eventRecurrenceRule": recur,
        "timeReminders": [], "reminderPipelineVersion": 1,
    }
    # Due time is a local date marker (YYYY-MM-DD 23:59:59) plus `zone`, same
    # assumption as all-day events — not converted to UTC. The list query's
    # dateTime, below, IS an end-of-day instant in UTC.
    r = api("/app/task", body, tok)
    return (r.get("data") or {}).get("eventIds", [])


# routinePeriods: time-of-day slots. 1 and 3 verified from app traffic; 2 inferred.
ROUTINE_PERIODS = {"morning": 1, "afternoon": 2, "evening": 3}


def create_routine(title, *, category, periods=("morning",), start=None, stars=0,
                   emoji=None, description="", timer_seconds=None, tok=None):
    """Create a Routine: a daily task pinned to one or more times of day, in the
    Tasks tab. The server makes one independent recurring series per period.

    category     : who it's for -- a key from ECALENDAR_CATEGORIES or a raw id.
    periods      : any of "morning" (00:00-12:00), "afternoon" (12:00-18:00, not yet
                   verified), "evening" (18:00-24:00), or their ints 1/2/3.
    start        : 'YYYY-MM-DD' the routine starts. Defaults to today.
    stars        : reward stars per completion. Defaults to 0.
    emoji        : an emoji name, or None for no icon (what the app sends).
    timer_seconds: optional focus-timer length.

    Returns the new eventIds, one per period. NOTE: in testing (2026-09-13)
    `/app/task/delete` rejected those ids as "eventId is invalid" — to delete a
    routine series, resolve its id from `list_tasks` (the row whose
    `eventId == routineSourceEventId`) and call `delete_task(that_id, series=True)`.

    Request (captured from the app 2026-09-13, periods morning + evening):
      POST /app/task
      {"deviceId": "<DEVICE_ID>", "title": "...", "eventType": "2", "isRecurring": 1,
       "userCalendarCategoryIds": ["<CATEGORY_ID>"], "zone": -5, "emoji": null,
       "taskType": 0, "description": "", "taskTimeoutPenalty": 0,
       "taskTimeoutPenaltyStarPercent": "0.00", "taskTimeoutPenaltyStarCount": 0,
       "taskMode": 1, "routinePeriods": [1, 3], "routineStartDate": "2026-09-13",
       "timerDurationSeconds": null, "starCount": 0, "priority": "0",
       "eventRecurrenceRule": {"recurrenceUnit": 1, "recurrenceValue": "1",
         "weekDays": null, "recurrenceMonthOption": null, "eventRecurrenceRulesId": 0}}
    Response:
      {"code": 200, "msg": null,
       "data": {"createdCount": 2, "eventIds": ["<ID_MORNING>", "<ID_EVENING>"]}}
    """
    _require_setup()
    cat = resolve_category(category)
    if isinstance(cat, list):
        cat = cat[0]
    if isinstance(periods, (str, int)):
        periods = [periods]
    slots = []
    for p in periods:
        n = ROUTINE_PERIODS.get(p.strip().lower()) if isinstance(p, str) else p
        if n not in (1, 2, 3):
            raise ValueError(f"unknown routine period {p!r} -- use {sorted(ROUTINE_PERIODS)}")
        if n not in slots:
            slots.append(n)
    if not slots:
        raise ValueError("create_routine needs at least one period")
    d = (start or date.today().isoformat())[:10]
    body = {
        "deviceId": str(DEVICE), "title": title, "description": description,
        "eventType": "2", "taskType": 0, "taskMode": 1,
        "routinePeriods": slots, "routineStartDate": d,
        "isRecurring": 1,
        "eventRecurrenceRule": {"recurrenceUnit": DAILY, "recurrenceValue": "1",
                                "weekDays": None, "recurrenceMonthOption": None,
                                "eventRecurrenceRulesId": 0},
        "userCalendarCategoryIds": [cat], "zone": tz_offset(d),
        "emoji": emoji or None, "starCount": max(0, int(stars)), "priority": "0",
        "timerDurationSeconds": timer_seconds,
        "taskTimeoutPenalty": 0, "taskTimeoutPenaltyStarPercent": "0.00",
        "taskTimeoutPenaltyStarCount": 0,
    }
    r = api("/app/task", body, tok)
    return (r.get("data") or {}).get("eventIds", [])


def list_tasks(day=None, categories=None, tok=None):
    """Tasks/chores for a day ('YYYY-MM-DD', default today). categories = list of
    keys/ids to filter to (required -- passing None returns nothing). Returns rows."""
    _require_setup()
    d = datetime.strptime((day or date.today().isoformat())[:10], "%Y-%m-%d").date()
    off = tz_offset(d)
    end_utc = datetime(d.year, d.month, d.day, 23, 59, 59) - timedelta(hours=off)
    cats = resolve_category(categories) if categories else None
    if isinstance(cats, str):
        cats = [cats]
    body = {
        "isFrontHandleHidden": 1, "dateTime": end_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "language": "en", "pageSize": 1000, "filterOverdueMiscellaneous": 0,
        "userCalendarCategoryIds": cats, "deviceId": str(DEVICE), "zone": off,
        "appCurrentTime": time.strftime("%Y-%m-%d %H:%M:%S"), "use12HourFormat": True,
    }
    r = api("/app/task/list", body, tok)
    return (r.get("data") or {}).get("miscEventList") or []


def delete_task(event_id, *, series=False, tok=None):
    """Delete a task. series=True for a recurring chore (deleteMethod 2)."""
    _require_setup()
    return api("/app/task/delete",
               {"deviceId": str(DEVICE), "eventId": str(event_id),
                "deleteMethod": 2 if series else 0}, tok)


# ================================================================ account / summary  (read-only)

def whoami(tok=None):
    """The signed-in user. Cheapest way to check the token still works.

    Request:  GET /app/user/mine/info
    Response: {"code": 200, "data": {"userId": "<USER_ID>", "userName": "...",
               "email": "...", "userStatus": 1, "plusType": 0, "isSubscribe": 0, ...}}
    """
    r = api("/app/user/mine/info", None, tok, method="GET")
    return r.get("data") or {}


def family(tok=None):
    """Families/devices the account belongs to (the wall display shows up here).

    Request:  GET /app/family/list
    Response: {"code": 200, "data": [
                {"deviceId": "<DEVICE_ID>", "familyName": "...", "isOwner": true,
                 "devicePlatformEmail": "...@myecalendar.com", "auditStatus": 1,
                 "deviceCodes": [{"virtualDeviceId": "<DEVICE_ID>", "deviceType": 0,
                                  "loginStatus": 1, "deviceName": "..."}]}]}
    """
    r = api("/app/family/list", None, tok, method="GET")
    return r.get("data") or []


def _utc_end_of_day(d):
    off = tz_offset(d)
    return datetime(d.year, d.month, d.day, 23, 59, 59) - timedelta(hours=off), off


def summary(categories=None, tok=None):
    """Today's at-a-glance counts (what the app's home screen shows).

    categories: keys/ids to include; default every ECALENDAR_CATEGORIES entry. Synced
    feeds aren't in ECALENDAR_CATEGORIES, so pass their ids too if you want their
    events counted.

    Request:
      POST /app/user/summary/data
      {"isFrontHandleHidden": 1, "activeTaskCategoryIds": ["<CATEGORY_ID>", ...],
       "activeEventCategoryIds": ["<CATEGORY_ID>", ...], "deviceId": "<DEVICE_ID>",
       "language": "en", "appCurrentTime": "2026-09-13 05:13:15",   # UTC
       "zone": -5, "use12HourFormat": true, "handleCrossDay": 1}
    Response:
      {"code": 200, "data": {"todayEvents": [], "todayNotStartEventCount": 0,
       "todayNotCompletedMiscellaneousCount": 2, "listCount": 2, "categoryCount": 7,
       "photoCount": 1, "userPhotoAlbumCount": 1, "deviceStatus": 0, "hasDeviceCode": 1}}
    ("Miscellaneous" = tasks.)
    """
    _require_setup()
    cats = resolve_category(categories) if categories else list(CATEGORIES.values())
    if isinstance(cats, str):
        cats = [cats]
    body = {
        "isFrontHandleHidden": 1, "activeTaskCategoryIds": cats,
        "activeEventCategoryIds": cats, "deviceId": str(DEVICE), "language": "en",
        "appCurrentTime": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "zone": tz_offset(), "use12HourFormat": True, "handleCrossDay": 1,
    }
    r = api("/app/user/summary/data", body, tok)
    return r.get("data") or {}


def category_stats(day=None, tok=None):
    """Every category with its star balance and task progress for a day
    ('YYYY-MM-DD', default today).

    Request:
      POST /app/user/event/category/list/v2
      {"deviceId": "<DEVICE_ID>", "pageSize": 1000, "filterOverdueMiscellaneous": 1,
       "zone": -5, "appCurrentTime": "2026-09-13 05:13:14",        # UTC
       "dateTime": "2026-09-14 04:59:59"}                          # end of day, UTC
    Response:
      {"code": 200, "data": {"total": 0, "completed": 0, "pageTotal": 7, "categories": [
        {"userCalendarCategoryId": "<CATEGORY_ID>", "categoryName": "Kid A",
         "categoryType": 1, "starCount": 11, "completedMiscellaneousCount": 0,
         "totalMiscellaneousCount": 0, "syncCalenderId": null, ...}]}}
    (The v1 /category/list returns the same rows with all-time task counts instead.)
    """
    _require_device()
    d = datetime.strptime((day or date.today().isoformat())[:10], "%Y-%m-%d").date()
    end_utc, off = _utc_end_of_day(d)
    body = {
        "deviceId": str(DEVICE), "pageSize": 1000, "filterOverdueMiscellaneous": 1,
        "zone": off, "appCurrentTime": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "dateTime": end_utc.strftime("%Y-%m-%d %H:%M:%S"),
    }
    r = api("/app/user/event/category/list/v2", body, tok)
    return r.get("data") or {}


# ================================================================ notes

def note_full(tok=None):
    _require_setup()
    data = api("/app/note/sync/full", {"deviceId": str(DEVICE)}, tok).get("data") or {}
    notes = data.get("notes")
    if notes is None:
        raise RuntimeError("note/sync/full -> code=200 msg=missing notes")
    return notes


def _shrink_pillow(path, out):
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("Pillow is not installed") from e
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((1600, 1600))
        im.save(out, "JPEG", quality=78)


def _shrink_to(path, out):
    """Write a <=1600px jpeg to `out`. sips when available, else Pillow."""
    errors = []
    if shutil.which("sips"):
        try:
            subprocess.run(
                ["sips", "-Z", "1600", "-s", "format", "jpeg", "-s", "formatOptions", "78",
                 path, "--out", out],
                check=True, capture_output=True)
            return
        except (OSError, subprocess.CalledProcessError) as e:
            errors.append(f"sips: {e}")
    try:
        _shrink_pillow(path, out)
        return
    except RuntimeError as e:
        errors.append(str(e))
    raise RuntimeError(
        "could not shrink image over 800KB (needs macOS sips, or Pillow): " + "; ".join(errors)
    )


def _shrink(path):
    """Return (path, content_type, is_temp) for an upload of about 800KB or less.

    Oversized images are rewritten to a mode-0600 temp jpeg. macOS `sips` is
    preferred; Pillow is the fallback when sips is missing or fails. The caller
    deletes the file when `is_temp` is true.
    """
    ct = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    if os.path.getsize(path) <= 800_000:
        return path, ct, False
    fd, out = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        _shrink_to(path, out)
    except Exception:
        try:
            os.unlink(out)
        except OSError:
            pass
        raise
    return out, "image/jpeg", True


def attach_images(image_paths, tok=None):
    """presign -> S3 PUT -> confirm for each image. Returns attachment dicts for
    note['attachments']. Skips (with a warning) any that fail. Max 8.

    Images over ~800KB are shrunk with macOS `sips` when it is on PATH, otherwise
    with Pillow if that is installed. The temp file is removed either way.
    """
    _require_setup(need_instance=True)
    tok = tok or token()
    out = []
    for i, p in enumerate(list(image_paths)[:8]):
        temp_path = None
        try:
            path, ct, is_temp = _shrink(p)
            if is_temp:
                temp_path = path
            with open(path, "rb") as fh:
                data = fh.read()
            fname = f"img_{int(time.time())}_{i}." + ct.split("/")[1]
            pres = api("/app/note/attachment/presign",
                       {"deviceId": DEVICE, "fileName": fname, "contentType": ct}, tok).get("data") or {}
            key, up, remote = pres["remoteKey"], pres["uploadUrl"], pres["remoteUrl"]
            aid = int(key.rsplit("/", 1)[1].split(".")[0])
            put = urllib.request.Request(up, data=data, method="PUT",
                                         headers={"Content-Type": ct})
            with urllib.request.urlopen(put, timeout=90) as resp:
                status = getattr(resp, "status", None)
                if status != 200:
                    raise RuntimeError(
                        f"attachment upload failed: code={status} msg={getattr(resp, 'reason', None)}"
                    )
            api("/app/note/attachment/confirm",
                {"deviceId": DEVICE, "remoteKey": key, "remoteUrl": remote,
                 "thumbnailUrl": remote, "fileName": fname, "fileSize": len(data)}, tok)
            out.append({"attachmentId": aid, "remoteKey": key, "remoteUrl": remote,
                        "thumbnailUrl": remote, "fileName": fname, "fileSize": len(data),
                        "sortOrder": i})
        except Exception as e:
            print(f"  ! attachment {p} failed: {e}")
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
    return out


def _note_payload(title, content, html, attachments, *, archived=False):
    return {
        "title": title, "content": content,
        "contentRichHtml": html or "".join(f"<p>{p}</p>" for p in content.split("\n") if p.strip()),
        "profileIds": [], "attachments": attachments or [],
        "isPinned": False, "pinnedTime": None,
        "isArchived": archived, "archivedTime": _iso_now() if archived else None,
        "wasPinnedBeforeArchive": 0,
        "userCreatedAt": _iso_now(), "userEditedAt": _iso_now(), "sort": 5000,
    }


def _push(op, tok=None):
    _require_setup(need_instance=True)
    data = api("/app/note/sync/push",
               {"deviceId": DEVICE, "clientInstanceId": INSTANCE, "baseChangeId": None,
                "operations": [op]}, tok).get("data") or {}
    results = data.get("results") or []
    if not results:
        raise RuntimeError("note push not success: code=None msg=missing results")
    res = results[0]
    if not res.get("success"):
        raise RuntimeError(
            f"note push not success: code={res.get('code')} msg={res.get('msg') or res.get('status')}"
        )
    return res


def create_note(title, content, html=None, image_paths=None, tok=None):
    tok = tok or token()
    attachments = attach_images(image_paths, tok) if image_paths else []
    micros = int(time.time() * 1_000_000)
    lnid = f"note-{micros}"
    op = {"clientOpId": f"note:create:{lnid}:{micros + 1}", "opType": "UPSERT_NOTE",
          "localNoteId": lnid, "serverNoteId": None,
          "note": _note_payload(title, content, html, attachments)}
    return _push(op, tok)["serverNoteId"]


def update_note(server_note_id, *, title=None, content=None, html=None, tok=None):
    """Fetch the note, apply changes, push back. Preserves attachments."""
    tok = tok or token()
    n = next((x for x in note_full(tok) if x["noteId"] == str(server_note_id)), None)
    if not n:
        raise RuntimeError(f"note {server_note_id} not found")
    body = _note_payload(
        title if title is not None else n["title"],
        content if content is not None else n["content"],
        html if html is not None else n["contentRichHtml"],
        n.get("attachments", []))
    uc = n["userCreatedAt"]
    body["userCreatedAt"] = uc.replace(" ", "T") + ".000Z" if "T" not in uc else uc
    op = {"clientOpId": f"note:update:{n['localNoteId']}:{int(time.time()*1e6)}",
          "opType": "UPSERT_NOTE", "localNoteId": n["localNoteId"],
          "serverNoteId": n["noteId"], "note": body}
    return _push(op, tok)


def delete_note(server_note_id, *, archive_only=False, tok=None):
    tok = tok or token()
    n = next((x for x in note_full(tok) if x["noteId"] == str(server_note_id)), None)
    if not n:
        raise RuntimeError(f"note {server_note_id} not found")
    payload = _note_payload(n["title"], n["content"], n["contentRichHtml"],
                            n.get("attachments", []), archived=archive_only)
    op = {"clientOpId": f"note:{'update' if archive_only else 'delete'}:{n['localNoteId']}:{int(time.time()*1e6)}",
          "opType": "UPSERT_NOTE" if archive_only else "DELETE_NOTE",
          "localNoteId": n["localNoteId"], "serverNoteId": n["noteId"], "note": payload}
    return _push(op, tok)


# ---------------------------------------------------------------- meals
#
# The Meals tab (recipes + a per-day meal plan) is a separate feature from events
# and notes, with its own two-step model: a "recipe" (the food item, created once)
# gets attached to a date under a meal category (Breakfast/Lunch/Dinner/Snack) via
# a "meal plan" entry. Multiple meal-plan entries can point at the same recipe --
# that's the normal way to represent "pizza again this week."
#
# IMPORTANT: recipe names must be unique ACROSS THE WHOLE ACCOUNT, not just within
# one meal category -- creating a second recipe named "Pancakes" fails even if the
# first one is under a different category, and even against a *pre-existing* recipe
# you didn't create (the app seeds some common ones, e.g. a stock "Pancakes"). This
# is the norm rather than the exception when importing a school breakfast/lunch
# calendar, where the same handful of dishes repeat every week or month. Always go
# through create_recipe()/add_meal() below rather than calling POST /app/v2/recipe
# directly -- they look up an existing recipe by exact name first and reuse its id
# instead of erroring.
#
# mealDateTime is a local calendar-date marker ("YYYY-MM-DD 23:59:59") plus
# addZone. It is not converted to UTC. Same assumption as all-day events and
# task due times — verify on a device if a meal lands on the wrong day.


def resolve_meal_category(name):
    if not name:
        return None
    return _lookup_id(MEAL_CATEGORIES, name)  # pass through if already an id


def find_recipe(name, tok=None):
    """Look up an existing recipe (custom or one of the app's built-in ones) by exact
    name match. Returns its mealRecipeId, or None if nothing matches.
    A non-200 response raises (it is not treated as "no such recipe").

    Request:
      POST /app/v2/recipe/groupedList
      {"deviceId": "<DEVICE_ID>", "search": "Pancakes"}

    Response (trimmed -- real payload includes system recipe categories like
    "Breakfast & Brunch" too, each with its own `recipes` list):
      {"code": 200, "msg": null, "data": [
        {"mealCategoryId": "<BREAKFAST_CATEGORY_ID>", "categoryName": "Custom",
         "categoryColor": null, "categoryPriority": -1, "recipes": [
           {"mealRecipeId": "<EXISTING_RECIPE_ID>", "recipeName": "Pancakes",
            "sourceType": "custom", "isSystem": 0, "recipeDesc": null, "servings": 1,
            "mealCategoryId": "<BREAKFAST_CATEGORY_ID>", "mealCategoryName": "Breakfast"}
         ]}
      ]}
    """
    _require_device()
    tok = tok or token()
    r = api("/app/v2/recipe/groupedList", {"deviceId": str(DEVICE), "search": name}, tok)
    for group in r.get("data") or []:
        for rec in group.get("recipes") or []:
            if rec.get("recipeName") == name:
                return rec["mealRecipeId"]
    return None


def create_recipe(name, description="", tok=None):
    """Create a custom recipe, or reuse the existing one if `name` already matches
    something on the account (see the module-level note above -- this is common,
    not an edge case, so don't skip straight to the raw API call).

    Request:
      POST /app/v2/recipe
      {"deviceId": "<DEVICE_ID>", "recipeName": "Pancakes",
       "recipeDesc": "Applesauce Cup, 100% Fruit Juice, Milk",
       "servings": 1, "sourceType": "custom",
       "ingredients": [], "steps": [], "allergens": []}

    Response (success) -- `data` is the new id directly, not nested:
      {"code": 200, "msg": null, "data": "<RECIPE_ID>"}

    Response (duplicate name):
      {"code": 500, "msg": "Recipe name cannot be duplicate", "data": null}
    """
    _require_device()
    tok = tok or token()
    existing = find_recipe(name, tok)
    if existing:
        return existing
    r = api("/app/v2/recipe", {
        "deviceId": str(DEVICE), "recipeName": name, "recipeDesc": description,
        "servings": 1, "sourceType": "custom",
        "ingredients": [], "steps": [], "allergens": [],
    }, tok)
    return r.get("data")


def create_meal(recipe_id, date_str, *, category=None, remark="", tok=None):
    """Attach an existing recipe id to a date under a meal category.

    date_str : 'YYYY-MM-DD'. Sent as that local date at 23:59:59 plus addZone,
               not converted to UTC (see the meals note above).
    category : a key from ECALENDAR_MEAL_CATEGORIES, or a raw mealCategoryId.

    Request:
      POST /app/mealPlan
      {"apiVersion": "2.0", "mealPlanId": "", "deviceId": "<DEVICE_ID>",
       "mealRecipeId": "", "mealRecipeIdList": ["<RECIPE_ID>"],
       "mealDateTime": "2026-09-08 23:59:59", "mealRemark": "",
       "mealCategoryId": "<LUNCH_CATEGORY_ID>", "addZone": -5,
       "isRecurring": 0, "eventRecurrenceRule": null, "language": "en"}

    Response -- unlike event/add, this DOES return the new id(s):
      {"code": 200, "msg": null, "data": ["<MEAL_PLAN_ID>"]}
    """
    _require_device()
    tok = tok or token()
    cat = resolve_meal_category(category)
    if not cat:
        raise RuntimeError(
            "create_meal: no category given and ECALENDAR_MEAL_CATEGORIES is empty -- see SETUP.md")
    r = api("/app/mealPlan", {
        "apiVersion": "2.0", "mealPlanId": "", "deviceId": str(DEVICE),
        "mealRecipeId": "", "mealRecipeIdList": [recipe_id],
        "mealDateTime": f"{date_str} 23:59:59", "mealRemark": remark,
        "mealCategoryId": cat, "addZone": tz_offset(date_str),
        "isRecurring": 0, "eventRecurrenceRule": None, "language": "en",
    }, tok)
    return r.get("data")


def add_meal(name, date_str, *, description="", category=None, remark="", tok=None):
    """Convenience: create-or-reuse a recipe by name and attach it to a date in one
    call. This is what you want for importing a recurring menu (school lunch, meal
    prep plan, etc.) -- repeated names across different dates automatically share
    one recipe instead of erroring on the duplicate-name rule.

        ecal.add_meal("Pizza (Turkey Pepperoni or Cheese)", "2026-09-08",
                      description="Oven Roasted Broccoli, Fresh Fruit, Milk",
                      category="lunch")
    """
    _require_device()
    tok = tok or token()
    recipe_id = create_recipe(name, description, tok)
    return create_meal(recipe_id, date_str, category=category, remark=remark, tok=tok)


def list_meals(start_date, end_date, categories=None, tok=None):
    """List meal-plan entries in [start_date, end_date] ('YYYY-MM-DD').

    categories: list of ECALENDAR_MEAL_CATEGORIES keys/ids, or None for all of them.

    CAUTION: a meal category the user has toggled off in the app's Meals settings is
    silently excluded from the response -- code 200, just no rows for it, regardless
    of whether you pass its id here. If entries you know you created aren't showing
    up, check that before assuming the write failed.

    Request:
      POST /app/mealPlan/list
      {"deviceId": "<DEVICE_ID>",
       "mealCategoryIds": ["<BREAKFAST_CATEGORY_ID>", "<LUNCH_CATEGORY_ID>"],
       "startDate": "2026-09-01", "endDate": "2026-09-30", "language": "en", "zone": -5}

    Response (one row shown; real response has one per meal-plan entry):
      {"code": 200, "msg": null, "data": [
        {"mealPlanId": "<MEAL_PLAN_ID>", "mealCategoryId": "<LUNCH_CATEGORY_ID>",
         "categoryName": "Lunch", "mealRecipeId": "<RECIPE_ID>",
         "recipeName": "Pizza (Turkey Pepperoni or Cheese)",
         "mealDateTime": "2026-09-08 23:59:59",
         "dateDescription": "Tuesday, September 8, 2026", "mealRemark": ""}
      ]}
    """
    _require_device()
    tok = tok or token()
    cats = [resolve_meal_category(c) for c in categories] if categories else list(MEAL_CATEGORIES.values())
    r = api("/app/mealPlan/list", {"deviceId": str(DEVICE), "mealCategoryIds": cats,
            "startDate": start_date, "endDate": end_date, "language": "en",
            "zone": tz_offset(start_date)}, tok)
    return r.get("data") or []


if __name__ == "__main__":
    t = token()
    src = "env" if os.environ.get("ECALENDAR_TOKEN") else "plist"
    me = whoami(t)
    print(f"token ok ({src}): signed in as", me.get("userName") or me.get("userId"))
    _require_setup()
    print(f"{TIMEZONE} offset today:", tz_offset())
    ev = list_events(date.today().isoformat(), date.today().isoformat())
    print(f"events today: {len(ev)}")
    for e in ev[:5]:
        print("  ", e.get("startDatetime"), "|", e.get("title"))
