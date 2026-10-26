"""API-mocked tests for timezone conversion, recurrence, dedupe, and retries.

Nothing here contacts api.cd.myecalendar.com.
"""
import io
import json
import os
import sqlite3
import sys
import warnings
from datetime import date

import pytest

import ecal
import ecal_cache
import extract_ecal_config as extract


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("ECALENDAR_DEVICE", "device-1")
    monkeypatch.setenv("ECALENDAR_INSTANCE", "inst-1")
    monkeypatch.setenv("ECALENDAR_TIMEZONE", "America/New_York")
    monkeypatch.setenv(
        "ECALENDAR_CATEGORIES",
        json.dumps({"kid_a": "id-kid", "home": "id-home", "family": "id-family"}),
    )
    monkeypatch.delenv("ECALENDAR_DEFAULT_CATEGORY", raising=False)
    monkeypatch.delenv("ECALENDAR_MEAL_CATEGORIES", raising=False)
    monkeypatch.setenv("ECALENDAR_TOKEN", "test-token")
    ecal._refresh_from_env()
    monkeypatch.setattr(ecal, "_RETRY_BACKOFF", 0.0)


def _body(**overrides):
    kw = dict(
        all_day=False, description="", category="kid_a", location=None,
        reminders_min=(0,), recur=None, add_calendar_id=None,
    )
    kw.update(overrides)
    title = kw.pop("title", "Dentist")
    start = kw.pop("start")
    end = kw.pop("end", None)
    return ecal._event_body(title, start, end, **kw)


class _Resp:
    def __init__(self, body, status=200, reason="OK"):
        self._body = body if isinstance(body, bytes) else body.encode()
        self.status = status
        self.reason = reason

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code, msg, extra=None):
    import urllib.error
    payload = {"code": code, "msg": msg}
    if extra:
        payload["data"] = extra
    return urllib.error.HTTPError(
        "https://api.cd.myecalendar.com/x", code, "err", {},
        io.BytesIO(json.dumps(payload).encode()),
    )


# ---------------------------------------------------------------- timezone


def test_tz_offset_dst_new_york():
    assert ecal.tz_offset(date(2026, 1, 15)) == -5
    assert ecal.tz_offset(date(2026, 3, 7)) == -5
    # 2026-03-08 is the spring-forward Sunday; noon is already EDT.
    assert ecal.tz_offset(date(2026, 3, 8)) == -4
    assert ecal.tz_offset(date(2026, 7, 15)) == -4
    assert ecal.tz_offset(date(2026, 10, 31)) == -4
    # 2026-11-01 is the fall-back Sunday; noon is already EST.
    assert ecal.tz_offset(date(2026, 11, 1)) == -5


def test_tz_offset_fractional_zones(monkeypatch):
    monkeypatch.setattr(ecal, "TIMEZONE", "Asia/Kolkata")
    assert ecal.tz_offset(date(2026, 1, 15)) == 5.5
    monkeypatch.setattr(ecal, "TIMEZONE", "America/St_Johns")
    assert ecal.tz_offset(date(2026, 1, 15)) == -3.5
    assert ecal.tz_offset(date(2026, 7, 15)) == -2.5
    monkeypatch.setattr(ecal, "TIMEZONE", "Asia/Kathmandu")
    assert ecal.tz_offset(date(2026, 6, 1)) == 5.75


def test_unknown_zone_does_not_assume_eastern(monkeypatch):
    monkeypatch.setattr(ecal, "TIMEZONE", "Not/A_Real_Zone")
    with pytest.raises(RuntimeError, match="US Eastern"):
        ecal.tz_offset(date(2026, 1, 15))


def test_us_fallback_only_for_known_zones(monkeypatch):
    monkeypatch.setattr(ecal, "_try_zoneinfo", lambda: None)
    monkeypatch.setattr(ecal, "TIMEZONE", "America/Chicago")
    assert ecal.tz_offset(date(2026, 1, 15)) == -6
    assert ecal.tz_offset(date(2026, 7, 15)) == -5
    monkeypatch.setattr(ecal, "TIMEZONE", "Pacific/Auckland")
    with pytest.raises(RuntimeError, match="US Eastern"):
        ecal.tz_offset(date(2026, 7, 15))


def test_timed_event_body_converts_local_to_utc_across_dst():
    winter = _body(start="2026-01-15 15:30:00")
    assert winter["startDatetime"] == "2026-01-15 20:30:00"
    assert winter["endDatetime"] == "2026-01-15 21:30:00"  # +1h in local time
    assert winter["zone"] == -5
    assert winter["isAllDay"] == 0

    summer = _body(start="2026-07-15 15:30:00", end="2026-07-15 16:00:00")
    assert summer["startDatetime"] == "2026-07-15 19:30:00"
    assert summer["endDatetime"] == "2026-07-15 20:00:00"
    assert summer["zone"] == -4

    before = _body(start="2026-03-07 12:00:00", end="2026-03-07 13:00:00")
    after = _body(start="2026-03-08 12:00:00", end="2026-03-08 13:00:00")
    assert before["startDatetime"] == "2026-03-07 17:00:00"
    assert before["zone"] == -5
    assert after["startDatetime"] == "2026-03-08 16:00:00"
    assert after["zone"] == -4

    # Evening local time lands on the next UTC date. The zone stays the local date's.
    late = _body(start="2026-01-15 20:00:00", end="2026-01-15 21:00:00", reminders_min=())
    assert late["startDatetime"] == "2026-01-16 01:00:00"
    assert late["endDatetime"] == "2026-01-16 02:00:00"
    assert late["zone"] == -5
    assert late["timeReminders"] == []


def test_half_hour_zone_conversion(monkeypatch):
    monkeypatch.setattr(ecal, "TIMEZONE", "Asia/Kolkata")
    body = _body(start="2026-01-15 15:30:00", end="2026-01-15 16:00:00")
    assert body["startDatetime"] == "2026-01-15 10:00:00"
    assert body["zone"] == 5.5
    monkeypatch.setattr(ecal, "TIMEZONE", "America/St_Johns")
    body = _body(start="2026-01-15 12:00:00", end="2026-01-15 12:30:00")
    assert body["startDatetime"] == "2026-01-15 15:30:00"
    assert body["zone"] == -3.5


def test_all_day_task_and_meal_are_not_converted(monkeypatch):
    body = _body(start="2026-10-03", all_day=True, reminders_min=())
    assert body["startDatetime"] == "2026-10-03 00:00:00"
    assert body["endDatetime"] == "2026-10-03 23:59:59"
    assert body["isAllDay"] == 1
    assert body["timeReminders"] == [{"method": "popup", "timeUnit": "minute", "value": 540}]
    assert body["zone"] == -4  # October, EDT

    seen = []

    def fake_api(path, payload, tok=None, method="POST"):
        seen.append((path, payload))
        if path == "/app/v2/recipe/groupedList":
            return {"code": 200, "data": []}
        if path == "/app/v2/recipe":
            return {"code": 200, "data": "recipe-1"}
        if path == "/app/mealPlan":
            return {"code": 200, "data": ["meal-1"]}
        return {"code": 200, "data": {"eventIds": ["t1"]}}

    monkeypatch.setattr(ecal, "api", fake_api)
    monkeypatch.setenv("ECALENDAR_MEAL_CATEGORIES", json.dumps({"lunch": "lunch-id"}))
    ecal._refresh_from_env()
    ecal.create_task("Dishes", category="kid_a", due="2026-01-15")
    task = seen[-1][1]
    assert task["startDatetime"] == "2026-01-15 23:59:59"
    assert task["isAllDay"] == 1
    assert task["zone"] == -5
    ecal.add_meal("Pizza", "2026-01-15", category="lunch")
    meal = next(payload for path, payload in seen if path == "/app/mealPlan")
    assert meal["mealDateTime"] == "2026-01-15 23:59:59"
    assert meal["addZone"] == -5


def test_create_and_edit_send_utc_and_keep_origin(monkeypatch):
    seen = {}

    def fake_api(path, payload, tok=None, method="POST"):
        seen["path"] = path
        seen["body"] = payload
        return {"code": 200, "data": None}

    monkeypatch.setattr(ecal, "api", fake_api)
    ecal.create_event("Dentist", "2026-01-15 15:30:00", category="kid_a")
    assert seen["path"] == "/app/event/add"
    assert seen["body"]["startDatetime"] == "2026-01-15 20:30:00"

    ecal.edit_event(
        "evt-1", "Dentist", "2026-01-15 16:00:00", category="kid_a",
        origin_event_id="evt-1", origin_start="2026-01-15 20:30:00",
        origin_end="2026-01-15 21:30:00", update_method=0,
    )
    assert seen["path"] == "/app/event/edit/selective"
    assert seen["body"]["startDatetime"] == "2026-01-15 21:00:00"
    assert seen["body"]["originStartDatetime"] == "2026-01-15 20:30:00"
    assert seen["body"]["originEndDatetime"] == "2026-01-15 21:30:00"


# ---------------------------------------------------------------- recurrence / categories


def test_recurrence_and_event_body():
    rule = ecal.recurrence(ecal.WEEKLY, value=2, week_days=[1, 3], end="2026-12-15")
    assert rule == {
        "eventRecurrenceRulesId": None,
        "recurrenceValue": 2,
        "recurrenceUnit": 2,
        "weekDays": [1, 3],
        "recurrenceMonthOption": None,
        "recurrenceEndDatetime": "2026-12-15 23:59:59",
    }
    assert (ecal.DAILY, ecal.WEEKLY, ecal.MONTHLY, ecal.YEARLY) == (1, 2, 3, 4)
    # A full timestamp is not rewritten with 23:59:59.
    assert ecal.recurrence(ecal.DAILY, end="2026-12-15 18:00:00")["recurrenceEndDatetime"] == \
        "2026-12-15 18:00:00"
    body = _body(start="2026-09-15 16:00:00", end="2026-09-15 17:00:00", recur=rule)
    assert body["isRecurring"] == 1
    assert body["eventRecurrenceRule"]["weekDays"] == [1, 3]
    assert body["startDatetime"] == "2026-09-15 20:00:00"  # 16:00 EDT


def test_default_category_matches_readme(monkeypatch):
    # family wins over home and over dict order (kid_a is first).
    assert ecal.DEFAULT_CATEGORY == "family"
    assert ecal.resolve_category(None) == "id-family"
    monkeypatch.setenv("ECALENDAR_CATEGORIES", json.dumps({"kid_b": "id-b", "home": "id-home"}))
    monkeypatch.delenv("ECALENDAR_DEFAULT_CATEGORY", raising=False)
    ecal._refresh_from_env()
    assert ecal.DEFAULT_CATEGORY == "home"
    assert ecal.resolve_category(None) == "id-home"
    monkeypatch.setenv("ECALENDAR_CATEGORIES", json.dumps({"kid_b": "id-b", "kid_a": "id-a"}))
    ecal._refresh_from_env()
    assert ecal.DEFAULT_CATEGORY == "kid_b"
    monkeypatch.setenv("ECALENDAR_DEFAULT_CATEGORY", "kid_a")
    ecal._refresh_from_env()
    assert ecal.DEFAULT_CATEGORY == "kid_a"
    assert ecal.resolve_category(None) == "id-a"
    monkeypatch.delenv("ECALENDAR_DEFAULT_CATEGORY", raising=False)
    monkeypatch.setenv("ECALENDAR_CATEGORIES", json.dumps({"Family": "id-fam"}))
    ecal._refresh_from_env()
    assert ecal.DEFAULT_CATEGORY == "Family"
    assert ecal.resolve_category(None) == "id-fam"
    assert ecal.resolve_category("family") == "id-fam"


def test_invalid_categories_json(monkeypatch):
    monkeypatch.setenv("ECALENDAR_CATEGORIES", "{not json")
    with pytest.raises(RuntimeError, match="ECALENDAR_CATEGORIES"):
        ecal._refresh_from_env()
    monkeypatch.setenv("ECALENDAR_CATEGORIES", "[]")
    with pytest.raises(RuntimeError, match="JSON object"):
        ecal._refresh_from_env()
    monkeypatch.setenv("ECALENDAR_MEAL_CATEGORIES", "nope")
    monkeypatch.setenv("ECALENDAR_CATEGORIES", "{}")
    with pytest.raises(RuntimeError, match="ECALENDAR_MEAL_CATEGORIES"):
        ecal._refresh_from_env()


# ---------------------------------------------------------------- read side / dedupe


def _rows():
    return [
        # local 2026-01-15 20:00 EST
        {"eventId": "late", "title": "Late show", "isAllDay": 0,
         "startDatetime": "2026-01-16 01:00:00"},
        # local 2026-01-14 21:00 EST (UTC date is the 15th)
        {"eventId": "eve", "title": "Eve", "isAllDay": 0,
         "startDatetime": "2026-01-15 02:00:00"},
        {"eventId": "pic", "title": "Picture Day", "isAllDay": 1,
         "startDatetime": "2026-01-15 00:00:00"},
        {"eventId": "piano4", "title": "Piano lesson", "isAllDay": 0,
         "startDatetime": "2026-01-12 21:00:00"},  # 16:00 EST
        {"eventId": "piano6", "title": "Piano lesson", "isAllDay": 0,
         "startDatetime": "2026-01-12 23:00:00"},  # 18:00 EST
        {"eventId": "piano-next", "title": "Piano lesson", "isAllDay": 0,
         "startDatetime": "2026-01-19 21:00:00"},  # next week, 16:00 EST
        {"eventId": "weird", "title": "Weird", "startDatetime": "not-a-date"},
    ]


def test_list_events_filters_in_local_time(monkeypatch):
    monkeypatch.setattr(ecal, "api", lambda *a, **k: {"code": 200, "rows": _rows()})
    jan15 = {r["eventId"] for r in ecal.list_events("2026-01-15", "2026-01-15")}
    # ±1 local day: late (15th), eve (14th), picture (15th), weird (unparsed).
    assert "late" in jan15
    assert "pic" in jan15
    assert "eve" in jan15
    assert "weird" in jan15
    assert "piano4" not in jan15

    jan17 = {r["eventId"] for r in ecal.list_events("2026-01-17", "2026-01-17")}
    # Late show's UTC date is the 16th, which the old filter would keep for the 17th.
    assert "late" not in jan17
    jan16 = {r["eventId"] for r in ecal.list_events("2026-01-16", "2026-01-16")}
    # Eve is local Jan 14; its UTC date (Jan 15) used to fall inside Jan 16 ±1.
    assert "eve" not in jan16
    assert "late" in jan16  # local Jan 15 is within ±1 of Jan 16


def test_title_score_is_shared_and_conservative():
    # Old ecal.py formula scored this 1.0 (query words only) and would dedupe it.
    # The cache formula scored it 1/3. Both callers now use this one.
    long = "soccer practice tonight"
    assert ecal.title_score("soccer", long) == pytest.approx(1 / 3)
    assert ecal.title_score("soccer", long) < ecal.TITLE_MATCH
    assert ecal.title_score("picture day", "picture day school") == pytest.approx(2 / 3)
    assert ecal.title_score("Dentist visit", "dentist visit") == 1
    assert ecal.title_score("", "Dentist") == 0
    assert ecal.title_score("ab", "abcd extra") == 0
    rows = [{"title": long, "isAllDay": 1, "startDatetime": "2026-01-15 00:00:00", "eventId": "s"}]
    assert ecal.pick_duplicate(rows, "soccer", "2026-01-15") is None
    assert ecal.pick_duplicate(rows, "soccer practice tonight", "2026-01-15")["eventId"] == "s"


def test_find_duplicate_timed_instances_do_not_cross(monkeypatch):
    monkeypatch.setattr(ecal, "api", lambda *a, **k: {"code": 200, "rows": _rows()})
    assert ecal.find_duplicate("Piano lesson", "2026-01-12 16:00:00")["eventId"] == "piano4"
    assert ecal.find_duplicate("Piano lesson", "2026-01-12 18:00:00")["eventId"] == "piano6"
    assert ecal.find_duplicate("Piano lesson", "2026-01-19 16:00:00")["eventId"] == "piano-next"
    # 15 minutes is the tolerance; an hour away is a different instance.
    assert ecal.find_duplicate("Piano lesson", "2026-01-12 17:00:00") is None
    # Date only still matches the title within ±1 local day (best score; first wins ties).
    assert ecal.find_duplicate("Picture Day", "2026-01-15")["eventId"] == "pic"


def test_cache_local_dates_allday_flag_and_dedupe(monkeypatch, tmp_path):
    monkeypatch.setattr(ecal, "list_events", lambda *a, **k: _rows())
    db = str(tmp_path / "cache.db")
    ecal_cache.sync(db_path=db)
    cx = sqlite3.connect(db)
    flags = dict(cx.execute("SELECT title, allDay FROM events").fetchall())
    cx.close()
    assert flags["Picture Day"] == 1
    assert flags["Late show"] == 0
    assert flags["Piano lesson"] == 0

    on15 = {r["eventId"] for r in ecal_cache.events_on("2026-01-15", db_path=db)}
    assert {"late", "pic", "eve"} <= on15
    on17 = {r["eventId"] for r in ecal_cache.events_on("2026-01-17", db_path=db)}
    assert "late" not in on17

    hit = ecal_cache.find_duplicate("Piano lesson", "2026-01-12 16:00:00", db_path=db)
    assert hit["eventId"] == "piano4"
    assert ecal_cache.find_duplicate("Piano lesson", "2026-01-12 18:00:00", db_path=db)["eventId"] == "piano6"
    assert ecal_cache.find_duplicate("soccer", "2026-01-15", db_path=db) is None


# ---------------------------------------------------------------- retries / errors


def test_retry_timeout_and_5xx_only_when_idempotent(monkeypatch):
    monkeypatch.setenv("ECALENDAR_TOKEN", "tok")
    slept = []
    monkeypatch.setattr(ecal.time, "sleep", lambda s: slept.append(s))

    calls = {"n": 0}

    def timeout_then_ok(req, timeout=40):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("slow")
        return _Resp(b'{"code":200,"rows":[]}')

    monkeypatch.setattr(ecal.urllib.request, "urlopen", timeout_then_ok)
    assert ecal.api("/app/event/list", {"a": 1})["code"] == 200
    assert calls["n"] == 2
    assert slept == [0.0]

    calls["n"] = 0
    slept.clear()

    def always_timeout(req, timeout=40):
        calls["n"] += 1
        raise TimeoutError("slow")

    monkeypatch.setattr(ecal.urllib.request, "urlopen", always_timeout)
    with pytest.raises(TimeoutError):
        ecal.api("/app/event/add", {"a": 1})
    assert calls["n"] == 1
    assert slept == []

    calls["n"] = 0

    def server_error_then_ok(req, timeout=40):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(503, "busy", extra={"secret": "LEAK"})
        return _Resp(b'{"code":200,"data":{}}')

    monkeypatch.setattr(ecal.urllib.request, "urlopen", server_error_then_ok)
    assert ecal.api("/app/event/delete/selective", {"eventId": "1"})["code"] == 200
    assert calls["n"] == 2

    calls["n"] = 0

    def always_500(req, timeout=40):
        calls["n"] += 1
        raise _http_error(500, "down", extra={"secret": "LEAK"})

    monkeypatch.setattr(ecal.urllib.request, "urlopen", always_500)
    with pytest.raises(RuntimeError, match=r"code=500 msg=down") as ei:
        ecal.api("/app/task", {"a": 1})
    assert calls["n"] == 1
    assert "LEAK" not in str(ei.value)


def test_json_code_is_not_retried_and_payload_is_not_in_the_error(monkeypatch):
    monkeypatch.setenv("ECALENDAR_TOKEN", "tok")
    calls = {"n": 0}

    def fake(req, timeout=40):
        calls["n"] += 1
        return _Resp(b'{"code":500,"msg":"Recipe name cannot be duplicate","data":{"secret":"LEAK"}}')

    monkeypatch.setattr(ecal.urllib.request, "urlopen", fake)
    with pytest.raises(RuntimeError, match="code=500 msg=Recipe name cannot be duplicate") as ei:
        ecal.api("/app/event/list", {})
    assert calls["n"] == 1
    assert "LEAK" not in str(ei.value)

    def denied(req, timeout=40):
        return _Resp(b'{"code":401,"msg":"Account logged in on another device","data":{"secret":"LEAK"}}')

    monkeypatch.setattr(ecal.urllib.request, "urlopen", denied)
    with pytest.raises(ecal.TokenReplacedError, match="code=401") as ei:
        ecal.api("/app/event/list", {})
    assert "LEAK" not in str(ei.value)


def test_curl_fallback_uses_fail_with_body_and_hides_the_token(monkeypatch):
    import http.client
    monkeypatch.setenv("ECALENDAR_TOKEN", "super-secret-token")
    seen = {}

    def boom(req, timeout=40):
        raise http.client.IncompleteRead(b"partial")

    def fake_run(cmd, capture_output=True, timeout=90):
        seen["cmd"] = cmd
        class P:
            returncode = 0
            stdout = b'{"code":200,"rows":[]}'
            stderr = b""
        return P()

    monkeypatch.setattr(ecal.urllib.request, "urlopen", boom)
    monkeypatch.setattr(ecal.subprocess, "run", fake_run)
    assert ecal.api("/app/event/list", {"x": 1})["rows"] == []
    cmd = seen["cmd"]
    assert "--fail-with-body" in cmd
    assert "super-secret-token" not in cmd
    assert not any("Bearer" in part for part in cmd)

    def refuse_curl(*a, **k):
        raise AssertionError("creates must not fall back to curl")

    monkeypatch.setattr(ecal.subprocess, "run", refuse_curl)
    with pytest.raises(http.client.IncompleteRead):
        ecal.api("/app/event/add", {"x": 1})


def test_curl_http_error_uses_code_and_msg(monkeypatch):
    import http.client
    monkeypatch.setenv("ECALENDAR_TOKEN", "tok")

    def boom(req, timeout=40):
        raise http.client.IncompleteRead(b"")

    def fake_run(cmd, capture_output=True, timeout=90):
        class P:
            returncode = 22
            stdout = b'{"code":503,"msg":"unavailable","data":{"secret":"LEAK"}}'
            stderr = b"curl: (22) the full error page should not surface"
        return P()

    monkeypatch.setattr(ecal.urllib.request, "urlopen", boom)
    monkeypatch.setattr(ecal.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match=r"code=503 msg=unavailable") as ei:
        ecal.api("/app/event/list", {})
    assert "LEAK" not in str(ei.value)
    assert "error page" not in str(ei.value)


def test_upload_bad_status_raises(monkeypatch, tmp_path, capsys):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"small-jpeg")

    def fake_api(path, payload, tok=None, method="POST"):
        if path.endswith("/presign"):
            return {"code": 200, "data": {
                "remoteKey": "admin/notes/1/5.jpg",
                "uploadUrl": "https://example.invalid/put",
                "remoteUrl": "https://cdn.example/5.jpg",
            }}
        return {"code": 200, "data": {}}

    monkeypatch.setattr(ecal, "api", fake_api)
    monkeypatch.setattr(ecal.urllib.request, "urlopen", lambda *a, **k: _Resp(b"", status=403, reason="Forbidden"))
    monkeypatch.setenv("ECALENDAR_TOKEN", "tok")
    out = ecal.attach_images([str(img)])
    assert out == []
    err = capsys.readouterr().out
    assert "code=403" in err
    assert "AssertionError" not in err


def test_shrink_uses_private_temp_and_cleans_up(monkeypatch, tmp_path):
    small = tmp_path / "s.jpg"
    small.write_bytes(b"x" * 10)
    path, ct, is_temp = ecal._shrink(str(small))
    assert (path, ct, is_temp) == (str(small), "image/jpeg", False)

    big = tmp_path / "big.png"
    big.write_bytes(b"x" * 800_001)

    def fake_run(cmd, capture_output=True, check=True, timeout=None):
        out = cmd[cmd.index("--out") + 1]
        with open(out, "wb") as fh:
            fh.write(b"jpeg")
        class P:
            returncode = 0
        return P()

    monkeypatch.setattr(ecal.shutil, "which", lambda name: "/usr/bin/sips" if name == "sips" else None)
    monkeypatch.setattr(ecal.subprocess, "run", fake_run)
    path, ct, is_temp = ecal._shrink(str(big))
    try:
        assert is_temp and ct == "image/jpeg"
        assert os.stat(path).st_mode & 0o777 == 0o600
        assert os.path.basename(path) != os.path.basename(str(big))
    finally:
        os.unlink(path)

    created = {}
    real_mkstemp = ecal.tempfile.mkstemp

    def wrapped(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        created["name"] = name
        return fd, name

    monkeypatch.setattr(ecal.tempfile, "mkstemp", wrapped)
    monkeypatch.setattr(ecal.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(RuntimeError, match="sips"):
        ecal._shrink(str(big))
    assert created["name"]
    assert not os.path.exists(created["name"])


def test_auth_file_permission_warning(tmp_path):
    path = tmp_path / "ECALENDAR_INFO.bin"
    path.write_text('ECALENDAR_DEVICE=dev\nECALENDAR_CATEGORIES={"family":"1"}\n')
    path.chmod(0o644)
    with pytest.warns(UserWarning, match="readable by other"):
        ecal.load_auth_file(str(path))
    path.chmod(0o600)
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        ecal.load_auth_file(str(path))


def test_extract_write_is_mode_0600(tmp_path):
    import plistlib
    plist = {
        "flutter.user": json.dumps({"token": "sekrit-token"}),
        "flutter.deviceMemory": "42",
        "flutter.notesSyncClientInstanceId": "uuid",
        "flutter.allEventCategories_42": json.dumps([
            {"categoryName": "Family", "userCalendarCategoryId": "fam"},
            {"categoryName": "School", "userCalendarCategoryId": "sch", "syncCalenderId": "gcal"},
        ]),
    }
    text = extract.build_lines(plist)
    assert "ECALENDAR_TOKEN=sekrit-token" in text
    assert "ECALENDAR_DEFAULT_CATEGORY=family" in text
    assert "sch" not in text.split("ECALENDAR_CATEGORIES=", 1)[1].splitlines()[0]

    src = tmp_path / "app.plist"
    with open(src, "wb") as fh:
        plistlib.dump(plist, fh)
    dest = tmp_path / "ECALENDAR_INFO.bin"
    dest.write_text("old")
    dest.chmod(0o644)
    out, err = io.StringIO(), io.StringIO()
    from contextlib import redirect_stderr, redirect_stdout
    with redirect_stdout(out), redirect_stderr(err):
        extract.main(["--plist", str(src), "--write", str(dest)])
    assert "sekrit-token" not in out.getvalue()
    assert "sekrit-token" not in err.getvalue()
    assert "0600" in err.getvalue()
    assert dest.stat().st_mode & 0o777 == 0o600
    assert "sekrit-token" in dest.read_text()


def test_require_device_points_at_setup():
    ecal.DEVICE = ""
    with pytest.raises(RuntimeError, match="SETUP.md") as ei:
        ecal._require_device()
    assert "template/" not in str(ei.value)
