import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.mock_client import MOCK_TFE_CLIENT_IDS, PORTAL_APP_ID
from app.service import DeltaService, is_subscription_excluded


@pytest.fixture
def app():
    return create_app({
        "TESTING": True, "MOCK_MODE": True, "SECRET_KEY": "test",
        "LOCAL_ADMIN_PASSWORD": "pw", "LOCAL_ADMIN_PASSWORD_HASH": "",
        "TFE_CLIENT_IDS": list(MOCK_TFE_CLIENT_IDS),
    })


@pytest.fixture
def service(app):
    return app.extensions["delta_service"]


@pytest.fixture
def client(app):
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        token = s["csrf"]
    resp = c.post("/login", data={"username": "admin", "password": "pw", "csrf": token})
    assert resp.status_code == 302
    return c


def window(days):
    end = datetime.now(timezone.utc)
    return end - timedelta(days=days), end


# ------------------------------------------------------------------ service
def test_subscription_exclusion_patterns():
    sub = {"id": "AAAA", "name": "Contoso-Sandbox-Alice"}
    assert is_subscription_excluded(sub, ["*sandbox*"])
    assert is_subscription_excluded(sub, ["aaaa"])
    assert not is_subscription_excluded(sub, ["*prod*"])


def test_changes_are_time_ordered_and_only_successful_writes(service):
    start, end = window(30)
    data = service.get_changes(start=start, end=end, excluded=[])
    changes = data["changes"]
    assert changes
    stamps = [c["timestamp"] for c in changes]
    assert stamps == sorted(stamps, reverse=True)
    for c in changes:
        assert not c["operation"].lower().endswith("/read")
        assert "/list" not in c["operation"].lower()
        assert c["resource_name"]
        assert c["caller"]


def test_begin_and_end_events_are_deduplicated(service):
    start, end = window(90)
    changes = service.get_changes(start=start, end=end, excluded=[])["changes"]
    keys = [(c["correlation_id"], c["resource_id"], c["operation"]) for c in changes]
    assert len(keys) == len(set(keys))


def test_default_exclusions_remove_sandbox_and_preprod(service):
    start, end = window(30)
    data = service.get_changes(start=start, end=end)
    names = {s["name"] for s in data["subscriptions"]}
    assert not any("Sandbox" in n or "PreProd" in n for n in names)
    assert all(c["subscription_name"] in names for c in data["changes"])


def test_terraform_toggle(service):
    start, end = window(30)
    everything = service.get_changes(start=start, end=end, excluded=[])["changes"]
    no_tf = service.get_changes(start=start, end=end, excluded=[], hide_terraform=True)["changes"]
    assert any(c["is_terraform"] for c in everything)
    assert not any(c["is_terraform"] for c in no_tf)
    assert len(no_tf) < len(everything)


def test_resource_graph_before_after_join(service):
    start, end = window(10)
    changes = service.get_changes(start=start, end=end, excluded=[])["changes"]
    with_props = [c for c in changes if c["property_changes"]]
    assert with_props
    p = with_props[0]["property_changes"][0]
    assert p["path"] and p["after"] is not None
    # Older than 14 days: no Resource Graph data
    old_start = datetime.now(timezone.utc) - timedelta(days=80)
    old = service.get_changes(start=old_start, end=old_start + timedelta(days=30), excluded=[])["changes"]
    assert old and not any(c["property_changes"] for c in old)


def test_portal_client_detected(service):
    start, end = window(90)
    changes = service.get_changes(start=start, end=end, excluded=[])["changes"]
    portal = [c for c in changes if c["client_id"] == PORTAL_APP_ID]
    assert portal and all(c["client"] == "Azure Portal" for c in portal)


def test_scope_resource_group(service):
    sub = next(s for s in service.all_subscriptions() if s["name"] == "Contoso-Online-Prod")
    start, end = window(90)
    data = service.get_changes("resourcegroup", sub["id"], "rg-web-prod", start, end)
    assert data["changes"]
    assert {c["resource_group"] for c in data["changes"]} == {"rg-web-prod"}


def test_scope_management_group(service):
    start, end = window(90)
    data = service.get_changes("managementgroup", "contoso-platform", None, start, end, excluded=[])
    assert {s["name"] for s in data["subscriptions"]} == {
        "Contoso-Connectivity", "Contoso-Identity", "Contoso-Management"}


def test_invalid_scope_input_rejected(service):
    from app.service import ScopeError
    with pytest.raises(ScopeError):
        service.get_changes("resourcegroup", "not-a-guid", "rg")
    sub = service.all_subscriptions()[0]
    with pytest.raises(ScopeError):
        service.get_changes("resourcegroup", sub["id"], "rg' or 1==1")


def test_clickops_report(service):
    start, end = window(90)
    changes = service.get_changes(start=start, end=end, excluded=[])["changes"]
    report = DeltaService.clickops_report(changes, top_n=5)
    assert len(report["top"]) == 5
    totals = [r["total"] for r in report["top"]]
    assert totals == sorted(totals, reverse=True)
    assert all("@" in r["caller"] for r in report["top"])
    assert report["clickops_changes"] == sum(1 for c in changes if c["is_clickops"])
    portal = DeltaService.clickops_report(changes, top_n=5, portal_only=True)
    assert portal["clickops_changes"] < report["clickops_changes"]


def test_csv_escapes_formulas():
    out = DeltaService.to_csv([{
        "timestamp": "t", "resource_name": "=HYPERLINK(\"x\")", "property_changes": [],
    }])
    row = list(csv.DictReader(io.StringIO(out)))[0]
    assert row["resource_name"].startswith("'=")


# ------------------------------------------------------------------ routes
def test_requires_login(app):
    c = app.test_client()
    assert c.get("/").status_code == 302
    assert c.get("/api/changes").status_code == 401


def test_bad_login(app):
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        token = s["csrf"]
    resp = c.post("/login", data={"username": "admin", "password": "nope", "csrf": token})
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data


def test_login_requires_csrf(app):
    c = app.test_client()
    resp = c.post("/login", data={"username": "admin", "password": "pw"})
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    assert c.get("/api/changes").status_code == 401


def test_pages_render(client):
    assert client.get("/").status_code == 200
    assert client.get("/report").status_code == 200


def test_api_endpoints(client):
    cfg = client.get("/api/config").get_json()
    assert cfg["mock_mode"] and cfg["top_n"] == 10
    scopes = client.get("/api/scopes").get_json()
    assert len(scopes["subscriptions"]) == 9
    assert any(s["excluded_by_default"] for s in scopes["subscriptions"])

    changes = client.get("/api/changes?scope=tenant&hide_tf=1").get_json()
    assert changes["changes"] and not any(c["is_terraform"] for c in changes["changes"])

    start = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    report = client.get("/api/report", query_string={"top": 3, "start": start}).get_json()
    assert len(report["top"]) == 3

    resp = client.get("/api/changes.csv?exclude=")
    assert resp.mimetype == "text/csv"
    assert resp.data.startswith(b"timestamp,")
    resp = client.get("/api/report.csv")
    assert resp.data.startswith(b"rank,")


def test_api_bad_scope(client):
    resp = client.get("/api/changes?scope=subscription&id=nope")
    assert resp.status_code == 400


def test_refresh_requires_csrf(client):
    assert client.post("/api/refresh").status_code == 403
    client.get("/")  # page render issues the token, as in the browser
    with client.session_transaction() as s:
        token = s["csrf"]
    assert client.post("/api/refresh", headers={"X-CSRF-Token": token}).status_code == 200
