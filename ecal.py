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
import http.client, json, os, subprocess, tempfile, time, urllib.request, urllib.error
from datetime import datetime, date, timedelta

BASE = "https://api.cd.myecalendar.com"
PLIST = os.path.expanduser(
    "~/Library/Containers/com.fujia.ecalendar/Data/Library/Preferences/com.fujia.ecalendar.plist")


# calendar category (a.k.a. profile) ids -- set ECALENDAR_CATEGORIES to a JSON object,
# e.g. {"family": "123...", "kid_a": "456...", "kid_b": "789..."}. See SETUP.md.
# meal category ids (Breakfast/Lunch/Dinner/Snack, as configured in the app's Meals
# tab) -- set ECALENDAR_MEAL_CATEGORIES to a JSON object, e.g.
# {"breakfast": "123...", "lunch": "456..."}. See SETUP.md.

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
    CATEGORIES = json.loads(os.environ.get("ECALENDAR_CATEGORIES", "{}"))
    DEFAULT_CATEGORY = os.environ.get("ECALENDAR_DEFAULT_CATEGORY") or next(iter(CATEGORIES.values()), None)
    MEAL_CATEGORIES = json.loads(os.environ.get("ECALENDAR_MEAL_CATEGORIES", "{}"))

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


def token():
    env = os.environ.get("ECALENDAR_TOKEN")
    if env:
        return env.strip()
    import plistlib
    with open(PLIST, "rb") as f:
        d = plistlib.load(f)
    return json.loads(d["flutter.user"])["token"]


def load_auth_file(path="./ECALENDAR_INFO.bin"):
    """Parse a KEY=value auth file into os.environ and refresh module config,
    so `import ecal; ecal.load_auth_file()` just works. Pass the path to your
    own file if it lives elsewhere.

    Handles the backslash-escaped JSON in ECALENDAR_CATEGORIES -- do NOT
    `source` this file with bash, bash quote-removal mangles it.
    """
    p = os.path.expanduser(path)
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


def tz_offset(on=None):
    """UTC offset in hours for ECALENDAR_TIMEZONE (DST-aware) on a given date."""
    d = on or date.today()
    if isinstance(d, str):
        d = datetime.strptime(d[:10], "%Y-%m-%d").date()
    try:
        from zoneinfo import ZoneInfo
        off = datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo(TIMEZONE)).utcoffset()
        return int(off.total_seconds() // 3600)
    except Exception:
        # fallback DST rule: 2nd Sunday March .. 1st Sunday November (US only)
        def nth_sun(y, m, n):
            first = date(y, m, 1)
            first_sun = first + timedelta((6 - first.weekday()) % 7)
            return first_sun + timedelta(7 * (n - 1))
        start, end = nth_sun(d.year, 3, 2), nth_sun(d.year, 11, 1)
        std, dst = _US_TZ_OFFSETS.get(TIMEZONE, _US_TZ_OFFSETS["America/New_York"])
        return dst if start <= d < end else std


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


def _check_auth(path, r):
    # Auth failures come back as HTTP 200 with code 401 in the body, e.g.
    #   {"code": 401, "msg": "Account logged in on another device", "data": null}
    if isinstance(r, dict) and r.get("code") == 401:
        raise TokenReplacedError(
            f"{path} -> 401: {r.get('msg')}. The token is no longer valid -- a newer "
            "sign-in replaced it. Get the current one (python3 extract_ecal_config.py "
            "on a Mac signed into eCalendar) and update ECALENDAR_TOKEN.")
    return r


def api(path, body, tok=None, method="POST"):
    """Call an endpoint. POST sends `body` as JSON; method="GET" ignores `body`
    (put query parameters in `path`)."""
    tok = tok or token()
    data = json.dumps(body).encode() if method == "POST" else None
    headers = _headers(tok)
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
        except (http.client.IncompleteRead, http.client.RemoteDisconnected,
                ConnectionResetError) as e:
            # The API sometimes sends truncated chunked responses that urllib
            # can't finish reading. curl handles the same response fine, so
            # retry through curl -- except for creates (/add): a create that
            # raised may still have landed server-side, and re-firing it
            # would make a duplicate. Callers must verify with
            # find_duplicate() before retrying a failed create.
            if not _retry_safe(path):
                raise
            raw = _curl_post(path, data, headers)
        if _looks_gzipped(raw):
            import gzip
            raw = gzip.decompress(raw)
        return _check_auth(path, json.loads(raw.decode()))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path} -> {e.code}: {e.read().decode()[:400]}")


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
            ["curl", "-sS", "--max-time", str(timeout), *body_args,
             BASE + path, "-H", "@" + hf.name],
            capture_output=True, timeout=timeout + 30)
        if p.returncode != 0:
            raise RuntimeError(f"curl fallback for {path} failed: {p.stderr.decode()[:200]}")
        return p.stdout
    finally:
        for f in (hf.name, bf.name):
            try:
                os.unlink(f)
            except OSError:
                pass


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def resolve_category(name):
    """Map a short key ('family', 'kid_a') to its category id. `name` may already be
    an id (passed through). Falls back to ECALENDAR_DEFAULT_CATEGORY."""
    name = name or DEFAULT_CATEGORY
    if not name:
        return None
    if isinstance(name, (list, tuple)):
        return [resolve_category(n) for n in name]
    return CATEGORIES.get(str(name).strip().lower(), name)


# ================================================================ events

def list_events(start_date, end_date, tok=None):
    """start_date / end_date = 'YYYY-MM-DD' (inclusive). Returns event rows whose start
    date falls in [start_date-1d, end_date+1d]. The API rejects ranges < 7 days, so this
    always fetches a widened window and filters client-side."""
    _require_setup()
    s = datetime.strptime(start_date, "%Y-%m-%d").date()
    e = datetime.strptime(end_date, "%Y-%m-%d").date()
    fetch_s = s - timedelta(days=3)
    fetch_e = max(e + timedelta(days=1), fetch_s + timedelta(days=9))
    r = api("/app/event/list", {"deviceId": str(DEVICE),
            "startDatetime": f"{fetch_s.isoformat()} 00:00:00",
            "endDatetime": f"{fetch_e.isoformat()} 23:59:59"}, tok)
    if r.get("code") not in (200, None):
        raise RuntimeError(f"event/list -> {r.get('code')}: {r.get('msg')}")
    lo, hi = s - timedelta(days=1), e + timedelta(days=1)
    out = []
    for row in r.get("rows", []):
        try:
            d = datetime.strptime((row.get("startDatetime") or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            out.append(row); continue
        if lo <= d <= hi:
            out.append(row)
    return out


def events_on(day, tok=None):
    """Convenience: event rows near a single 'YYYY-MM-DD'."""
    return list_events(day, day, tok)


def find_duplicate(title, day, tok=None):
    """An existing event row that looks like the same thing within +/-1 day, else None.
    Match = >=60% overlap of significant title words. Use before create_event."""
    import re
    def words(t):
        return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2}
    want = words(title)
    if not want:
        return None
    for row in events_on(day, tok):
        have = words(row.get("title"))
        if have and len(want & have) / max(len(want), 1) >= 0.6:
            return row
    return None


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
            st = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
            end = (st + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        sdt, edt = start, end
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
                    accept one minute-based reminder).
    all_day=False : `start`/`end` = 'YYYY-MM-DD HH:MM:SS' LOCAL wall time. `end`
                    defaults to start + 1h. reminders_min = minutes-before popups.
    category      : a key from ECALENDAR_CATEGORIES, or a raw id. Defaults to
                    ECALENDAR_DEFAULT_CATEGORY.
    recur         : an eventRecurrenceRule dict (see recurrence()), or None.

    Returns {"code":200,"data":null} -- NO id. Dedupe with find_duplicate() first.
    """
    _require_setup()
    body = _event_body(title, start, end, all_day=all_day, description=description,
                       category=category, location=location, reminders_min=reminders_min,
                       recur=recur, add_calendar_id=add_calendar_id)
    r = api("/app/event/add", body, tok)
    if r.get("code") != 200:
        raise RuntimeError(f"event/add failed: {r}")
    return r


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
    """Update an event. For a single recurring instance pass update_method (0=this /
    1=all / 2=this+future) + origin_event_id + origin_start + origin_end from the row."""
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
    r = api("/app/event/edit/selective", body, tok)
    if r.get("code") != 200:
        raise RuntimeError(f"event/edit failed: {r}")
    return r


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
    r = api("/app/event/add", body, tok)
    if r.get("code") != 200:
        raise RuntimeError(f"anniversary add failed: {r}")
    return r


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
    r = api("/app/task", body, tok)
    if r.get("code") != 200:
        raise RuntimeError(f"task/add failed: {r}")
    return r.get("data", {}).get("eventIds", [])


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

    Returns the new eventIds, one per period (each is its own series root, so
    delete_task(event_id, series=True) should remove that period's routine --
    routine deletes haven't been captured yet).

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
    if r.get("code") != 200:
        raise RuntimeError(f"routine add failed: {r}")
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
    if r.get("code") not in (200, None):
        raise RuntimeError(f"task/list -> {r.get('code')}: {r.get('msg')}")
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
    if r.get("code") != 200:
        raise RuntimeError(f"user/mine/info -> {r.get('code')}: {r.get('msg')}")
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
    if r.get("code") != 200:
        raise RuntimeError(f"family/list -> {r.get('code')}: {r.get('msg')}")
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
    if r.get("code") != 200:
        raise RuntimeError(f"summary/data -> {r.get('code')}: {r.get('msg')}")
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
    if r.get("code") != 200:
        raise RuntimeError(f"category/list/v2 -> {r.get('code')}: {r.get('msg')}")
    return r.get("data") or {}


# ================================================================ notes

def note_full(tok=None):
    _require_setup()
    return api("/app/note/sync/full", {"deviceId": str(DEVICE)}, tok)["data"]["notes"]


def _shrink(path):
    """Return (path, content_type) <= ~800KB. macOS `sips` (no webp) -> jpeg."""
    ct = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    if os.path.getsize(path) <= 800_000:
        return path, ct
    out = tempfile.mktemp(suffix=".jpg")
    subprocess.run(["sips", "-Z", "1600", "-s", "format", "jpeg", "-s", "formatOptions", "78",
                    path, "--out", out], check=True, capture_output=True)
    return out, "image/jpeg"


def attach_images(image_paths, tok=None):
    """presign -> S3 PUT -> confirm for each image. Returns attachment dicts for
    note['attachments']. Skips (with a warning) any that fail. Max 8. Needs `sips`
    (macOS) only when an image is larger than ~800KB."""
    _require_setup(need_instance=True)
    tok = tok or token()
    out = []
    for i, p in enumerate(list(image_paths)[:8]):
        try:
            path, ct = _shrink(p)
            data = open(path, "rb").read()
            fname = f"img_{int(time.time())}_{i}." + ct.split("/")[1]
            pres = api("/app/note/attachment/presign",
                       {"deviceId": DEVICE, "fileName": fname, "contentType": ct}, tok)["data"]
            key, up, remote = pres["remoteKey"], pres["uploadUrl"], pres["remoteUrl"]
            aid = int(key.rsplit("/", 1)[1].split(".")[0])
            put = urllib.request.Request(up, data=data, method="PUT",
                                         headers={"Content-Type": ct})
            with urllib.request.urlopen(put, timeout=90) as r:
                assert r.status == 200
            api("/app/note/attachment/confirm",
                {"deviceId": DEVICE, "remoteKey": key, "remoteUrl": remote,
                 "thumbnailUrl": remote, "fileName": fname, "fileSize": len(data)}, tok)
            out.append({"attachmentId": aid, "remoteKey": key, "remoteUrl": remote,
                        "thumbnailUrl": remote, "fileName": fname, "fileSize": len(data),
                        "sortOrder": i})
        except Exception as e:
            print(f"  ! attachment {p} failed: {e}")
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
    res = api("/app/note/sync/push",
              {"deviceId": DEVICE, "clientInstanceId": INSTANCE, "baseChangeId": None,
               "operations": [op]}, tok)["data"]["results"][0]
    if not res.get("success"):
        raise RuntimeError(f"note push not success: {res}")
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


def _require_device():
    if not DEVICE:
        raise RuntimeError("missing setup: ECALENDAR_DEVICE -- see template/SETUP.md")


def resolve_meal_category(name):
    if not name:
        return None
    return MEAL_CATEGORIES.get(name.strip().lower(), name)  # pass through if already an id


def find_recipe(name, tok=None):
    """Look up an existing recipe (custom or one of the app's built-in ones) by exact
    name match. Returns its mealRecipeId, or None if nothing matches.

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
    if r.get("code") != 200:
        return None
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
    if r.get("code") != 200:
        raise RuntimeError(f"recipe create failed: {r}")
    return r["data"]


def create_meal(recipe_id, date_str, *, category=None, remark="", tok=None):
    """Attach an existing recipe id to a date under a meal category.

    date_str : 'YYYY-MM-DD'.
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
    if r.get("code") != 200:
        raise RuntimeError(f"mealPlan create failed: {r}")
    return r["data"]


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
    if r.get("code") != 200:
        raise RuntimeError(f"mealPlan/list -> {r.get('code')}: {r.get('msg')}")
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
