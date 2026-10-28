"""Gateway tests. All eCalendar HTTP is mocked; nothing contacts the real API."""
import json
from datetime import date

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import ecal  # noqa: E402
from gateway import app as gw  # noqa: E402


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("ECALENDAR_DEVICE", "device-1")
    monkeypatch.setenv("ECALENDAR_TIMEZONE", "America/Chicago")
    monkeypatch.setenv("ECALENDAR_CATEGORIES", json.dumps({"family": "id-family"}))
    monkeypatch.setenv("ECALENDAR_TOKEN", "tok-0")
    for k in ("ECAL_EMAIL", "ECAL_PASSWORD", "GATEWAY_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    ecal._refresh_from_env()
    monkeypatch.setattr(ecal, "_RETRY_BACKOFF", 0.0)


class FakeAPI:
    """Stands in for ecal.api: records calls, 401s any token not in `valid`."""

    def __init__(self, valid=("tok-0",)):
        self.valid = set(valid)
        self.calls = []

    def __call__(self, path, body, tok=None, method="POST"):
        self.calls.append((path, body, tok))
        if path == "/app/user/login":
            new = f"tok-{len([c for c in self.calls if c[0] == path])}"
            self.valid = {new}
            return {"code": 200, "data": {"token": new}}
        if tok not in self.valid:
            raise ecal.TokenReplacedError(f"{path} -> code=401 msg=Account logged in on another device")
        if path == "/app/user/mine/info":
            return {"code": 200, "data": {"userId": "u1", "userName": "Kyle", "email": "k@x"}}
        if path == "/app/user/event/category/list":
            return {"code": 200, "rows": [{"userCalendarCategoryId": 1, "categoryName": "Family",
                                           "categoryColor": "#FF0000", "categoryType": 0,
                                           "syncCalenderId": None}]}
        if path == "/app/event/list":
            s, e = body["startDatetime"][:10], body["endDatetime"][:10]
            n = (date.fromisoformat(e) - date.fromisoformat(s)).days + 1
            assert 7 <= n <= 50, n
            return {"code": 200, "rows": [{"eventId": f"ev-{s}", "isAllDay": 1,
                                           "startDatetime": f"{s} 00:00:00"}]}
        return {"code": 200, "msg": "ok", "data": None}

    def paths(self, p):
        return [c for c in self.calls if c[0] == p]


def client(fake, monkeypatch, **kw):
    monkeypatch.setattr(ecal, "api", fake)
    session = kw.pop("session", None) or gw.Session(
        token="tok-0", email=kw.pop("email", None), password=kw.pop("password", None),
        min_backoff=0, login_fn=ecal.login)
    return TestClient(gw.create_app(session=session, cache_ttl=kw.pop("ttl", 60),
                                    api_key=kw.pop("api_key", ""))), session


def test_chunk_range():
    ch = ecal._chunk_range(date(2026, 1, 1), date(2026, 4, 30))
    assert ch[0] == (date(2026, 1, 1), date(2026, 2, 19))
    assert all((e - s).days + 1 <= 50 for s, e in ch)
    assert all((e - s).days + 1 >= 7 for s, e in ch)
    assert ch[-1][1] == date(2026, 4, 30)
    # contiguous coverage
    days = set()
    for s, e in ch:
        days |= {s.toordinal() + i for i in range((e - s).days + 1)}
    assert days == set(range(date(2026, 1, 1).toordinal(), date(2026, 4, 30).toordinal() + 1))


def test_list_events_chunks_long_range(monkeypatch):
    fake = FakeAPI()
    monkeypatch.setattr(ecal, "api", fake)
    rows = ecal.list_events("2026-01-01", "2026-06-30", "tok-0")
    assert len(fake.paths("/app/event/list")) >= 4
    assert len({r["eventId"] for r in rows}) == len(rows)


def test_health_and_reads_cached(monkeypatch):
    fake = FakeAPI()
    c, _ = client(fake, monkeypatch)
    assert c.get("/health").json()["ok"] is True
    assert c.get("/whoami").json()["userName"] == "Kyle"
    c.get("/whoami")
    assert len(fake.paths("/app/user/mine/info")) == 1
    cats = c.get("/categories").json()
    assert cats == [{"id": "1", "name": "Family", "color": "#FF0000", "type": 0,
                     "external": False}]
    assert c.get("/profiles").json() == cats


def test_events_range_chunked_and_trimmed(monkeypatch):
    fake = FakeAPI()
    c, _ = client(fake, monkeypatch)
    r = c.get("/events", params={"start": "2026-01-01", "end": "2026-05-01"}).json()
    assert len(fake.paths("/app/event/list")) == 3
    for row in r["rows"]:
        assert "2026-01-01" <= row["startDatetime"][:10] <= "2026-05-01"
    assert c.get("/events", params={"start": "x", "end": "2026-01-01"}).status_code == 400


def test_api_key(monkeypatch):
    c, _ = client(FakeAPI(), monkeypatch, api_key="s3cret")
    assert c.get("/health").status_code == 200
    assert c.get("/whoami").status_code == 401
    assert c.get("/whoami", headers={"X-API-Key": "nope"}).status_code == 401
    assert c.get("/whoami", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_replaced_token_without_credentials_is_503(monkeypatch):
    fake = FakeAPI(valid=())
    c, s = client(fake, monkeypatch)
    r = c.get("/whoami")
    assert r.status_code == 503
    assert "another login" in r.json()["detail"]
    assert s.status()["last_replaced_at"]
    assert not fake.paths("/app/user/login")


def test_relogin_on_401_then_backoff(monkeypatch):
    fake = FakeAPI(valid=())
    c, s = client(fake, monkeypatch, email="k@x", password="pw")
    assert c.get("/whoami").status_code == 200
    assert len(fake.paths("/app/user/login")) == 1
    assert s.token == "tok-1"
    # Another device steals it again immediately -> backoff floor stops a login storm.
    s.min_backoff = 3600
    s._next_login_at = s._clock() + 3600
    fake.valid = set()
    c.app.state.cache.clear()
    r = c.get("/whoami")
    assert r.status_code == 503 and "backing off" in r.json()["detail"]
    assert len(fake.paths("/app/user/login")) == 1


def test_login_failure_backs_off_exponentially():
    t = [0.0]
    calls = []

    def bad_login(e, p):
        calls.append(1)
        raise RuntimeError("code=500 msg=bad password")

    s = gw.Session(token=None, email="k@x", password="pw", min_backoff=10,
                   max_backoff=25, clock=lambda: t[0], login_fn=bad_login)
    with pytest.raises(gw.SessionUnavailable):
        s.get_token()
    with pytest.raises(gw.SessionUnavailable, match="backing off"):
        s.get_token()
    assert len(calls) == 1
    t[0] = 10
    with pytest.raises(gw.SessionUnavailable):
        s.get_token()
    assert s._next_login_at == 30  # 10 + 20
    t[0] = 30
    with pytest.raises(gw.SessionUnavailable):
        s.get_token()
    assert s._next_login_at == 55  # capped at 25


def test_writes_pass_through_and_clear_cache(monkeypatch):
    fake = FakeAPI()
    c, _ = client(fake, monkeypatch)
    c.get("/whoami")
    assert c.post("/events", json={"title": "Dentist"}).json()["code"] == 200
    path, body, _ = fake.calls[-1]
    assert path == "/app/event/add" and body == {"title": "Dentist", "deviceId": "device-1"}
    c.put("/events/42", json={"title": "Dentist 2"})
    assert fake.calls[-1][0] == "/app/event/edit/selective"
    assert fake.calls[-1][1]["eventId"] == "42"
    c.delete("/events/42", params={"series": "true"})
    assert fake.calls[-1][0] == "/app/event/delete/selective"
    assert fake.calls[-1][1]["deleteMethod"] == 2
    c.get("/whoami")
    assert len(fake.paths("/app/user/mine/info")) == 2  # cache cleared by writes


def test_upstream_error_is_502(monkeypatch):
    def boom(path, body, tok=None, method="POST"):
        raise RuntimeError(f"{path} -> code=500 msg=validation")
    c, _ = client(boom, monkeypatch)
    r = c.get("/whoami")
    assert r.status_code == 502 and "validation" in r.json()["detail"]
