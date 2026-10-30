import json, os
from datetime import date
import pytest
from fastapi.testclient import TestClient

from famcal.app import create_app
from famcal.core import Service, _norm_rrule
from famcal.db import DB
from famcal.sources import FixtureSource, GoogleSource, from_google, to_google, TAG_KEY

H = {"X-API-Key": "k"}


@pytest.fixture
def env(tmp_path):
    db = DB(str(tmp_path / "f.db"))
    fx = FixtureSource(str(tmp_path / "fx.json"))
    svc = Service(db, fx)
    c = TestClient(create_app(svc, api_key="k", data_dir=str(tmp_path), start_sync=False))
    return svc, fx, c


def test_auth_required(env):
    _, _, c = env
    assert c.get("/api/profiles").status_code == 401
    assert c.get("/api/profiles", headers=H).status_code == 200
    assert c.get("/health").json()["ok"]


def test_profiles_and_family(env):
    _, _, c = env
    ids = [p["id"] for p in c.get("/api/profiles", headers=H).json()]
    assert ids == ["jack", "liv", "mom", "dad", "bruce", "family"]


def test_phone_added_untagged_goes_family_and_totag(env):
    svc, fx, c = env
    fx.seed("shared", [{"id": "g1", "title": "Dentist", "start": "2026-11-02T09:00", "end": "2026-11-02T10:00",
                        "all_day": False, "status": "confirmed", "updated": "1"}])
    svc.sync()
    it = c.get("/api/instances?start=2026-11-02&end=2026-11-02", headers=H).json()["items"]
    assert it[0]["profiles"] == ["family"] and it[0]["needs_tag"]
    assert [e["id"] for e in c.get("/api/totag", headers=H).json()] == ["g1"]
    # tap-to-tag: Jack + Dad, written to source extendedProperties too
    c.put("/api/events/g1/tags", json={"profiles": ["jack", "dad"]}, headers=H)
    assert fx.data["shared"]["g1"]["tags"] == ["jack", "dad"]
    assert c.get("/api/totag", headers=H).json() == []
    # an edit made on a phone (title change) keeps tags
    fx.update("shared", "g1", {"title": "Dentist (Jack)"})
    svc.sync()
    assert svc.event("g1")["profiles"] == ["jack", "dad"]


def test_rules(env):
    svc, fx, c = env
    fx.seed("shared", [
        {"id": "a", "title": "Swim lessons", "start": "2026-11-03T17:00", "end": "2026-11-03T18:00", "updated": "1"},
        {"id": "b", "title": "Liv dentist", "start": "2026-11-03", "end": "2026-11-03", "all_day": True, "updated": "1"},
        {"id": "c", "title": "Sara book club", "start": "2026-11-04T19:00", "end": "2026-11-04T21:00", "updated": "1"}])
    fx.seed("soccer", [{"id": "s", "title": "Game vs Eagles", "start": "2026-11-05T10:00", "end": "2026-11-05T11:00", "updated": "1"}])
    svc.sync()
    assert svc.event("a")["profiles"] == ["jack", "liv"]
    assert svc.event("b")["profiles"] == ["liv"]
    assert svc.event("c")["profiles"] == ["mom"]
    assert svc.event("s")["profiles"] == ["jack"]
    assert fx.data["shared"]["a"]["tags"] == ["jack", "liv"]  # rule result mirrored to Google
    assert "tags" not in fx.data["soccer"]["s"]  # read-only feed not written


def test_history_rule(env):
    svc, fx, c = env
    fx.seed("shared", [{"id": "p1", "title": "Piano", "start": "2026-11-02T16:00", "end": "2026-11-02T17:00", "updated": "1"}])
    svc.sync()
    c.put("/api/events/p1/tags", json={"profiles": ["liv"]}, headers=H)
    fx.seed("shared", [{"id": "p2", "title": "piano!", "start": "2026-11-09T16:00", "end": "2026-11-09T17:00", "updated": "1"}])
    svc.sync()
    assert svc.event("p2")["profiles"] == ["liv"]


def test_tags_survive_server_loss(tmp_path):
    fx = FixtureSource(str(tmp_path / "fx.json"))
    s1 = Service(DB(str(tmp_path / "a.db")), fx)
    fx.seed("shared", [{"id": "x", "title": "Thing", "start": "2026-11-02", "end": "2026-11-02", "all_day": True, "updated": "1"}])
    s1.sync(); s1.set_tags("x", ["bruce", "dad"])
    s2 = Service(DB(str(tmp_path / "b.db")), fx)  # fresh DB = rebuilt server
    s2.sync()
    assert s2.event("x")["profiles"] == ["bruce", "dad"]


def test_create_edit_delete_event(env):
    svc, fx, c = env
    e = c.post("/api/events", json={"title": "Swim", "start": "2026-11-07T09:00", "end": "2026-11-07T10:00",
                                    "profiles": ["jack", "liv"]}, headers=H).json()
    assert e["profiles"] == ["jack", "liv"] and fx.data["shared"][e["id"]]["tags"] == ["jack", "liv"]
    e2 = c.patch(f"/api/events/{e['id']}", json={"title": "Swim meet", "start": "2026-11-07T10:00", "end": "2026-11-07T12:00"}, headers=H).json()
    assert e2["title"] == "Swim meet" and e2["profiles"] == ["jack", "liv"]
    assert c.post("/api/events", json={"title": ""}, headers=H).status_code == 400
    assert c.post("/api/events", json={"title": "x", "start": "bad"}, headers=H).status_code == 400
    c.delete(f"/api/events/{e['id']}", headers=H)
    assert c.get("/api/instances?start=2026-11-07&end=2026-11-07", headers=H).json()["items"] == []


def test_recurrence_until_utc_and_exdates(env):
    svc, fx, c = env
    fx.seed("shared", [{"id": "r", "title": "Jack's Soccer", "start": "2026-07-23T18:30", "end": "2026-07-23T19:30",
                        "rrule": "FREQ=WEEKLY;UNTIL=20260806;BYDAY=TH", "exdates": ["2026-07-30T18:30"], "updated": "1"}])
    svc.sync()
    it = c.get("/api/instances?start=2026-07-20&end=2026-08-20", headers=H).json()["items"]
    assert [i["start"] for i in it] == ["2026-07-23T18:30", "2026-08-06T18:30"]
    assert "UNTIL=20260625T185900" in _norm_rrule("FREQ=DAILY;UNTIL=20260625T235900Z")


def test_compat_events_roundtrip(env):
    svc, fx, c = env
    svc.db.x("UPDATE profiles SET ecal_id='2045961353543335937' WHERE id='jack'")
    body = {"title": "Field trip", "isAllDay": 0, "startDatetime": "2026-11-12 14:00:00",
            "endDatetime": "2026-11-12 20:00:00", "userCalendarCategoryIds": ["2045961353543335937"], "eventType": 0}
    r = c.post("/events", json=body, headers=H).json()
    assert r["code"] == 200 and r["data"]["profiles"] == ["jack"]
    rows = c.get("/events?start=2026-11-12&end=2026-11-12", headers=H).json()["rows"]
    assert rows[0]["localStart"] == "2026-11-12T08:00"  # 14:00Z = 8:00 CST
    assert rows[0]["startDatetime"] == "2026-11-12 14:00:00"
    assert rows[0]["userCalendarCategoryIds"] == ["2045961353543335937"]
    ad = c.post("/events", json={"title": "No school", "isAllDay": 1, "startDatetime": "2026-11-13 00:00:00",
                                 "endDatetime": "2026-11-13 23:59:59", "userCalendarCategoryIds": ["liv"]}, headers=H).json()
    assert ad["data"]["profiles"] == ["liv"]
    cats = c.get("/profiles", headers=H).json()
    assert any(x["id"] == "2045961353543335937" and x["name"] == "Jack" for x in cats)


def test_chores_stars_rewards(env):
    svc, fx, c = env
    ch = c.post("/api/chores", json={"title": "Feed Bruce", "profile_id": "jack", "stars": 2, "days": "1111111"}, headers=H).json()
    today = c.get("/api/chores", headers=H).json()
    assert today[0]["id"] == ch["id"] and not today[0]["done"]
    assert c.post(f"/api/chores/{ch['id']}/toggle", json={}, headers=H).json() == {"done": True, "stars": 2}
    assert c.post(f"/api/chores/{ch['id']}/toggle", json={}, headers=H).json() == {"done": False, "stars": 0}
    c.post(f"/api/chores/{ch['id']}/toggle", json={}, headers=H)
    rw = c.post("/api/rewards", json={"title": "Ice cream", "cost": 2}, headers=H).json()
    assert c.post(f"/api/rewards/{rw['id']}/redeem", json={"profile_id": "liv"}, headers=H).status_code == 400
    assert c.post(f"/api/rewards/{rw['id']}/redeem", json={"profile_id": "jack"}, headers=H).json() == {"stars": 0}


def test_weekday_chores(env):
    svc, _, _ = env
    svc.add_chore({"title": "Trash", "profile_id": "dad", "days": "0000100", "start_date": "2026-01-01"})
    assert [c["title"] for c in svc.chores_for(date(2026, 10, 30))] == ["Trash"]  # Friday
    assert svc.chores_for(date(2026, 10, 29)) == []


def test_lists(env):
    _, _, c = env
    L = c.get("/api/lists", headers=H).json()
    lid = L[0]["id"]
    it = c.post(f"/api/lists/{lid}/items", json={"text": "Milk"}, headers=H).json()
    c.patch(f"/api/items/{it['id']}", json={"done": 1}, headers=H)
    c.post(f"/api/lists/{lid}/clear-done", headers=H)
    assert c.get("/api/lists", headers=H).json()[0]["items"] == []


def test_google_mapping():
    g = {"id": "e1", "summary": "Swim", "start": {"dateTime": "2026-11-07T15:00:00Z"},
         "end": {"dateTime": "2026-11-07T16:00:00Z"}, "updated": "u",
         "extendedProperties": {"private": {TAG_KEY: "jack,liv"}}}
    ev = from_google(g)
    assert ev["start"] == "2026-11-07T09:00" and ev["tags"] == ["jack", "liv"]
    ad = from_google({"id": "e2", "summary": "Trip", "start": {"date": "2026-11-07"}, "end": {"date": "2026-11-09"},
                      "recurrence": ["RRULE:FREQ=YEARLY", "EXDATE;VALUE=DATE:20271107"]})
    assert ad["end"] == "2026-11-08" and ad["rrule"] == "FREQ=YEARLY" and ad["exdates"] == ["2027-11-07"] and ad["tags"] is None
    back = to_google({"title": "Trip", "start": "2026-11-07", "end": "2026-11-08", "all_day": True, "tags": ["dad"]})
    assert back["end"] == {"date": "2026-11-09"} and back["extendedProperties"]["private"][TAG_KEY] == "dad"


def test_google_source_with_fake_http(tmp_path):
    p = tmp_path / "tok.json"
    p.write_text(json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": "r"}))
    calls = []
    def http(method, url, headers, body):
        calls.append((method, url, json.loads(body) if body and method != "POST" or (body and url.endswith("/events")) else None))
        if "oauth2" in url:
            return 200, {"access_token": "AT", "expires_in": 3600}
        if method == "GET":
            return 200, {"items": [{"id": "g", "summary": "Hi", "start": {"date": "2026-11-01"}, "end": {"date": "2026-11-02"}}],
                         "nextSyncToken": "T"}
        return 200, {"id": "g", "summary": "Hi", "start": {"date": "2026-11-01"}, "end": {"date": "2026-11-02"}}
    src = GoogleSource(str(p), http=http)
    evs, full = src.list_events("fam@group.calendar.google.com")
    assert full and evs[0]["title"] == "Hi"
    evs, full = src.list_events("fam@group.calendar.google.com")
    assert not full and "syncToken=T" in calls[-1][1]
    src.set_tags("fam@group.calendar.google.com", "g", ["jack"])
    assert calls[-1][0] == "PATCH" and calls[-1][2] == {"extendedProperties": {"private": {TAG_KEY: "jack"}}}


def test_oauth_state_check(env, tmp_path):
    _, _, c = env
    c.post("/oauth/client", json={"client_id": "id", "client_secret": "sec"}, headers=H)
    u = c.get("/oauth/start", headers=H).json()["url"]
    assert "access_type=offline" in u and "code_challenge" in u
    r = c.post("/oauth/finish", json={"url": "http://127.0.0.1:8765/?state=wrong&code=x"}, headers=H)
    assert r.status_code == 400
    assert "sec" not in json.dumps(c.get("/oauth/status", headers=H).json())


def test_import_real_db_if_present(tmp_path):
    src = "/workspace/familycalendar/testdata/calendar_db"
    if not os.path.exists(src):
        pytest.skip("no calendar_db")
    from famcal import importer
    db = DB(str(tmp_path / "i.db")); fx = FixtureSource(str(tmp_path / "fx.json")); svc = Service(db, fx)
    st = importer.run(src, db, fx, today=date(2026, 10, 28))
    assert st["stars"] == {"dad": 54, "jack": 6, "liv": 6, "mom": 20}
    assert st["events"] > 400 and st["multi"] > 10
    svc.sync()
    assert svc.instances(date(2026, 10, 26), date(2026, 11, 8))


def test_method_override(env):
    _, _, c = env
    c.post("/api/lists", json={"name": "Zed"}, headers=H)
    lid = next(l["id"] for l in c.get("/api/lists", headers=H).json() if l["name"] == "Zed")
    it = c.post(f"/api/lists/{lid}/items", json={"text": "milk"}, headers=H).json()
    r = c.post(f"/api/items/{it['id']}", json={"done": 1}, headers={**H, "X-HTTP-Method-Override": "PATCH"})
    assert r.status_code == 200, r.text
    items = [x for l in c.get("/api/lists", headers=H).json() if l["id"] == lid for x in l["items"]]
    assert items[0]["done"] == 1


def test_service_account_token(tmp_path):
    import base64, json as _j
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from famcal.sources import GoogleSource
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    p = tmp_path / "sa.json"
    p.write_text(_j.dumps({"type": "service_account", "client_email": "fc@x.iam.gserviceaccount.com", "private_key": pem,
                           "private_key_id": "kid1", "token_uri": "https://oauth2.googleapis.com/token"}))
    seen = {}
    def http(method, url, headers, body):
        if url.endswith("/token"):
            from urllib.parse import parse_qs
            q = parse_qs(body.decode()); seen["grant"] = q["grant_type"][0]
            h, c, sig = q["assertion"][0].split(".")
            pad = lambda s: s + "=" * (-len(s) % 4)
            seen["claims"] = _j.loads(base64.urlsafe_b64decode(pad(c)))
            k.public_key().verify(base64.urlsafe_b64decode(pad(sig)), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())
            return 200, {"access_token": "T", "expires_in": 3600}
        seen["auth"] = headers["Authorization"]
        return 200, {"items": [{"id": "cal1", "summary": "Shared"}]}
    g = GoogleSource(str(p), http=http)
    assert g.mode == "service_account" and g.list_calendars()[0]["id"] == "cal1"
    assert seen["grant"].endswith("jwt-bearer") and seen["claims"]["iss"] == "fc@x.iam.gserviceaccount.com"
    assert seen["auth"] == "Bearer T"


def test_sa_preferred_over_oauth(tmp_path):
    import json as _j
    from famcal.sources import make_source
    (tmp_path / "google_token.json").write_text(_j.dumps({"client_id": "c", "client_secret": "s", "refresh_token": "r"}))
    assert make_source(str(tmp_path)).main.mode == "oauth"
    (tmp_path / "google_sa.json").write_text(_j.dumps({"type": "service_account", "client_email": "e", "private_key": "x"}))
    assert make_source(str(tmp_path)).main.mode == "service_account"


ICS = """BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a1\r\nSUMMARY:Practice\\, Field 3\r\nDTSTART;TZID=America/Chicago:20261103T173000\r\nDTEND;TZID=America/Chicago:20261103T190000\r\nLOCATION:Park\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:a2\r\nSUMMARY:Game\r\nDTSTART:20261107T150000Z\r\nDTEND:20261107T163000Z\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:a3\r\nSUMMARY:Tournament\r\nDTSTART;VALUE=DATE:20261114\r\nDTEND;VALUE=DATE:20261116\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""


def test_ics_and_routing(env):
    from famcal.sources import IcsSource, RoutingSource
    svc, fx, c = env
    evs = {e["title"]: e for e in IcsSource(fetch=lambda u: ICS).list_events("https://x/cal.ics")[0]}
    assert evs["Practice, Field 3"]["start"] == "2026-11-03T17:30"
    assert evs["Game"]["start"] == "2026-11-07T09:00"  # UTC -> Chicago
    assert evs["Tournament"]["all_day"] and evs["Tournament"]["end"] == "2026-11-15"
    # route the soccer calendar to the feed while the main source stays the same
    svc.source = RoutingSource(svc.source.main if hasattr(svc.source, "main") else svc.source, IcsSource(fetch=lambda u: ICS))
    svc.source.name = "google"  # pretend Google mode so google_id is honoured
    svc.db.x("UPDATE calendars SET google_id='https://x/cal.ics' WHERE id='soccer'")
    svc.db.x("UPDATE calendars SET google_id='shared' WHERE id='shared'")
    assert svc.sync() >= 0, svc.last_sync_error
    rows = c.get("/api/instances?start=2026-11-01&end=2026-11-20", headers=H).json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    soc = [r for r in rows if r["cal"] == "soccer"]
    assert len(soc) == 3 and all(r["profiles"] == ["jack"] for r in soc)


def test_native_events_survive_full_sync(env):
    svc, fx, c = env
    svc.db.x("""INSERT INTO events(id,cal,title,description,location,start,end,all_day,exdates,origin,updated,deleted)
                VALUES('n1','shared','Native only','','','2026-11-05','2026-11-05',1,'[]','ecal-native','x',0)""")
    assert svc.sync() >= 0
    assert svc.db.one("SELECT deleted FROM events WHERE id='n1'")["deleted"] == 0


def test_phase2_meta_chores_lists(tmp_path):
    from fastapi.testclient import TestClient
    from famcal.app import create_app
    from famcal.core import Service
    from famcal.db import DB
    from famcal.sources import FixtureSource, to_google
    svc = Service(DB(":memory:"), FixtureSource(str(tmp_path / "fx.json")))
    c = TestClient(create_app(svc, api_key="", data_dir=str(tmp_path), start_sync=False))
    ev = c.post("/api/events", json={"title": "Bday", "start": "2026-11-02", "all_day": True,
                                      "rrule": "FREQ=YEARLY", "reminders": [1440], "anniversary": True}).json()
    assert ev["reminders"] == [1440] and ev["anniversary"] and ev["rrule"] == "FREQ=YEARLY"
    ev = c.patch(f"/api/events/{ev['id']}", json={"reminders": [10, 60]}).json()
    assert ev["reminders"] == [10, 60] and ev["anniversary"]
    g = to_google({"title": "x", "reminders": [10], "anniversary": True, "tags": ["jack"]})
    assert g["reminders"]["overrides"][0]["minutes"] == 10
    assert g["extendedProperties"]["private"]["famcal_anniversary"] == "1"
    out = c.post("/api/chores", json={"title": "Feed dog", "profile_ids": ["jack", "liv"], "stars": 3,
                                      "days": "1010100", "time": "07:30", "start_date": "2026-11-01"}).json()
    assert len(out) == 2 and out[0]["grp"] == out[1]["grp"]
    h = c.get("/api/home", params={"start": "2026-11-02", "end": "2026-11-03", "chores_date": "2026-11-02"}).json()
    mine = [x for x in h["chores"] if x["title"] == "Feed dog"]
    assert len(mine) == 2 and mine[0]["time"] == "07:30" and "calendars" in h
    h = c.get("/api/home", params={"start": "2026-11-03", "end": "2026-11-03", "chores_date": "2026-11-03"}).json()
    assert not [x for x in h["chores"] if x["title"] == "Feed dog"]
    r = c.patch(f"/api/chores/{out[0]['id']}", json={"time": "08:00", "stars": 2}).json()
    assert r["time"] == "08:00" and r["stars"] == 2
    ls = c.post("/api/lists", json={"name": "Trip"}).json()
    lid = [l for l in ls if l["name"] == "Trip"][0]["id"]
    ls = c.patch(f"/api/lists/{lid}", json={"name": "Trip 2"}).json()
    assert any(l["name"] == "Trip 2" for l in ls)
    assert c.delete(f"/api/lists/{lid}").json()["ok"]


def test_phase3_move_event(tmp_path):
    from fastapi.testclient import TestClient
    from famcal.app import create_app
    from famcal.core import Service
    from famcal.db import DB
    from famcal.sources import FixtureSource
    fx = FixtureSource(str(tmp_path / "fx.json"))
    svc = Service(DB(":memory:"), fx)
    svc.db.x("INSERT INTO calendars(id,name,google_id,color,writable) VALUES('kids','Kids',NULL,'#00AA00',1)")
    c = TestClient(create_app(svc, api_key="", data_dir=str(tmp_path), start_sync=False))
    ev = c.post("/api/events", json={"title": "Move me", "start": "2026-11-02T10:00", "end": "2026-11-02T11:00",
                                      "profiles": ["jack"]}).json()
    assert ev["cal"] == "shared"
    ev2 = c.patch(f"/api/events/{ev['id']}", json={"cal": "kids", "title": "Moved"}).json()
    assert ev2["cal"] == "kids" and ev2["title"] == "Moved" and ev2["profiles"] == ["jack"]
    assert ev["id"] in fx.data["kids"] and ev["id"] not in fx.data.get("shared", {})
    assert c.patch(f"/api/events/{ev['id']}", json={"cal": "soccer"}).status_code == 400
    svc.sync()
    assert svc.event(ev["id"])["cal"] == "kids"


def test_phase3_upcoming_reminders(tmp_path):
    from datetime import datetime
    from famcal.core import Service
    from famcal.db import DB
    from famcal.sources import FixtureSource
    svc = Service(DB(":memory:"), FixtureSource(str(tmp_path / "fx.json")))
    svc.create_event({"title": "Dentist", "start": "2026-11-02T10:00", "end": "2026-11-02T11:00", "reminders": [0, 30]}, "shared")
    svc.create_event({"title": "Daily", "start": "2026-10-01T08:00", "end": "2026-10-01T08:15", "rrule": "FREQ=DAILY", "reminders": [5]}, "shared")
    svc.create_event({"title": "No rem", "start": "2026-11-02T12:00", "end": "2026-11-02T13:00"}, "shared")
    r = svc.upcoming_reminders(now=datetime(2026, 11, 2, 9, 0), hours=24)
    ats = [(x["title"], x["at"]) for x in r]
    assert ("Dentist", "2026-11-02T09:30") in ats and ("Dentist", "2026-11-02T10:00") in ats
    assert ("Daily", "2026-11-03T07:55") in ats and not any(t == "No rem" for t, _ in ats)
    assert ats == sorted(ats, key=lambda x: x[1])


def test_bundled_default_config_matches_example():
    here = os.path.dirname(__file__)
    a = json.load(open(os.path.join(here, "..", "famcal", "default_config.json")))
    b = json.load(open(os.path.join(here, "..", "..", "config.example.json")))
    assert a == b and b["profiles"] and b["app"]["camera"]["package"] == ""


def test_doors_proxy_fake_and_threshold(env, monkeypatch):
    svc, fx, c = env
    from famcal import config
    monkeypatch.setitem(config.get(), "doors", {"threshold_min": 5, "sensors": [
        {"name": "Front", "entity": "binary_sensor.front"}, {"name": "Garage", "entity": "binary_sensor.garage"}]})
    monkeypatch.delenv("HA_URL", raising=False)
    d = c.get("/api/doors", headers=H).json()
    assert d["threshold_min"] == 5 and d["alerts"] == [] and all(not x["ok"] for x in d["doors"])
    assert "entity" not in d["doors"][0]
    d = c.post("/api/doors/fake", headers=H, json={"name": "Garage", "minutes": 3}).json()
    assert d["alerts"] == []
    d = c.post("/api/doors/fake", headers=H, json={"name": "Garage", "minutes": 12}).json()
    assert [a["name"] for a in d["alerts"]] == ["Garage"] and d["alerts"][0]["open_min"] >= 12
    h = c.get("/api/home?start=2026-01-01&end=2026-01-02", headers=H).json()
    assert [(a["name"], a["open"]) for a in h["doors"]] == [("Front", False), ("Garage", True)]
    assert c.post("/api/doors/fake", headers=H, json={"clear_all": True}).json()["alerts"] == []


def test_doors_reads_ha_state(env, monkeypatch):
    from famcal.doors import Doors
    from famcal import config
    from datetime import datetime, timedelta, timezone
    monkeypatch.setitem(config.get(), "doors", {"threshold_min": 5, "sensors": [{"name": "Deck", "entity": "binary_sensor.deck"}]})
    d = Doors()
    since = (datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat()
    monkeypatch.setattr(d, "_ha_state", lambda e: {"state": "on", "last_changed": since})
    s = d.status()
    assert s["alerts"][0]["name"] == "Deck" and s["alerts"][0]["open_min"] == 7


def test_reward_redeem_subtracts_stars(env):
    svc, fx, c = env
    r = c.post("/api/rewards", headers=H, json={"title": "Pick the movie", "emoji": "🎬", "cost": 20}).json()
    c.post("/api/stars/adjust", headers=H, json={"profile_id": "jack", "delta": 25, "reason": "t"})
    assert c.post(f"/api/rewards/{r['id']}/redeem", headers=H, json={"profile_id": "jack"}).json()["stars"] == 5
    assert c.post(f"/api/rewards/{r['id']}/redeem", headers=H, json={"profile_id": "jack"}).status_code >= 400
