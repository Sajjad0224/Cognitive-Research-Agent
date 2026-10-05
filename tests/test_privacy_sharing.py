"""Phase 8: privacy (export/delete), login hardening, knowledge-graph view, read-only sharing."""
import pytest
from fastapi.testclient import TestClient

from cra.app import create_app, load_cases_dir
from tests.test_api import PW, Clock, signup


@pytest.fixture
def ctx():
    clock = Clock()
    app = create_app(load_cases_dir("cases"), clock=clock)
    return TestClient(app), clock, app


def start_and_conclude(c, h, correct=True, cited=None):
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h).json()["session_id"]
    if cited:
        c.post(f"/sessions/{sid}/ask", json={"text": "Show me the audit log"}, headers=h)
    text = ("Ken intentionally deleted the data to hide errors" if correct
           else "The server migration accidentally deleted the data")
    r = c.post(f"/sessions/{sid}/conclusion",
              json={"text": text, "confidence": 0.7, "cited_evidence": cited or []}, headers=h)
    assert r.status_code == 201, r.text
    return sid


# ---------------- security headers ----------------
def test_security_headers_present_on_every_response(ctx):
    c, _, _ = ctx
    for r in (c.get("/consent"), c.get("/"), c.get("/cases")):  # last one even unauthenticated
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'self'" in r.headers["content-security-policy"]


# ---------------- login hardening ----------------
def test_login_lockout_after_repeated_failures(ctx):
    c, clock, _ = ctx
    signup(c, "lock@x.co")
    for _ in range(5):
        assert c.post("/auth/login", json={"email": "lock@x.co", "password": "wrong"}).status_code == 401
    r = c.post("/auth/login", json={"email": "lock@x.co", "password": PW})
    assert r.status_code == 401 and "too many failed attempts" in r.json()["detail"]
    clock.now += __import__("datetime").timedelta(minutes=16)
    assert c.post("/auth/login", json={"email": "lock@x.co", "password": PW}).status_code == 200


def test_successful_login_clears_failure_count(ctx):
    c, _, _ = ctx
    signup(c, "clear@x.co")
    for _ in range(4):
        assert c.post("/auth/login", json={"email": "clear@x.co", "password": "wrong"}).status_code == 401
    assert c.post("/auth/login", json={"email": "clear@x.co", "password": PW}).status_code == 200
    for _ in range(4):
        assert c.post("/auth/login", json={"email": "clear@x.co", "password": "wrong"}).status_code == 401
    assert c.post("/auth/login", json={"email": "clear@x.co", "password": PW}).status_code == 200


# ---------------- privacy: export ----------------
def test_privacy_notice_is_public(ctx):
    c, _, _ = ctx
    r = c.get("/privacy/notice")
    assert r.status_code == 200 and "export_endpoint" in r.json()


def test_privacy_export_contains_account_events_and_reports(ctx):
    c, _, _ = ctx
    h = signup(c, "exp@x.co")
    sid = start_and_conclude(c, h, correct=True, cited=["E-04"])
    exp = c.get("/privacy/export", headers=h).json()
    assert exp["account"]["email"] == "exp@x.co"
    assert any(e["session_id"] == sid for e in exp["events"])
    assert len(exp["reports"]) == 1 and exp["reports"][0]["status"] == "solved"
    assert "pw_hash" not in str(exp)  # no credential material ever leaves via export


def test_privacy_export_is_per_user(ctx):
    c, _, _ = ctx
    a, b = signup(c, "ea@x.co"), signup(c, "eb@x.co")
    start_and_conclude(c, a)
    exp_b = c.get("/privacy/export", headers=b).json()
    assert exp_b["events"] == [] and exp_b["reports"] == []


# ---------------- privacy: account deletion ----------------
def test_account_deletion_erases_events_and_login(ctx):
    c, _, _ = ctx
    h = signup(c, "del@x.co")
    sid = start_and_conclude(c, h, correct=True)
    assert c.delete("/privacy/account", headers=h).status_code == 204
    assert c.get("/me", headers=h).status_code == 401                 # token is gone
    assert c.post("/auth/login", json={"email": "del@x.co", "password": PW}).status_code == 401
    h2 = signup(c, "del@x.co")                                        # email is free again
    assert c.get("/sessions", headers=h2).json() == []                # no leftover sessions
    assert c.get(f"/sessions/{sid}", headers=h2).status_code == 404   # old session truly gone


def test_account_deletion_does_not_affect_other_users(ctx):
    c, _, _ = ctx
    a, b = signup(c, "da@x.co"), signup(c, "db@x.co")
    sid_b = start_and_conclude(c, b, correct=True)
    c.delete("/privacy/account", headers=a)
    assert c.get(f"/sessions/{sid_b}", headers=b).status_code == 200
    assert c.get("/me", headers=b).status_code == 200


def test_erase_user_is_the_only_delete_path_and_other_users_events_remain_immutable(ctx):
    c, _, app = ctx
    a = signup(c, "ia@x.co")
    start_and_conclude(c, a)
    store = app.state.store
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError):
        store._conn.execute("DELETE FROM events")
    before = len(store._conn.execute("SELECT 1 FROM events").fetchall())
    n = store.erase_user("U-doesnotexist")
    assert n == 0
    after = len(store._conn.execute("SELECT 1 FROM events").fetchall())
    assert before == after


# ---------------- knowledge graph ----------------
def test_graph_reflects_only_discovered_evidence_and_own_hypotheses(ctx):
    c, _, _ = ctx
    h = signup(c, "g@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h).json()["session_id"]
    g0 = c.get(f"/sessions/{sid}/graph", headers=h).json()
    assert {n["id"] for n in g0["nodes"]} == {"E-01", "E-02", "E-03"}
    assert g0["edges"] == []
    c.post(f"/sessions/{sid}/ask", json={"text": "Show me the audit log"}, headers=h)
    hid = c.post(f"/sessions/{sid}/hypotheses", json={"statement": "Ken did it"}, headers=h).json()["hypothesis_id"]
    c.post(f"/sessions/{sid}/links", json={"hypothesis_id": hid, "evidence_id": "E-04", "relation": "supports"},
          headers=h)
    c.post(f"/sessions/{sid}/contradictions", json={"evidence_a": "E-02", "evidence_b": "E-04"}, headers=h)
    g1 = c.get(f"/sessions/{sid}/graph", headers=h).json()
    ids = {n["id"] for n in g1["nodes"]}
    assert "E-04" in ids and hid in ids
    rels = {(e["source"], e["target"], e["relation"]) for e in g1["edges"]}
    assert ("E-04", hid, "supports") in rels
    assert ("E-02", "E-04", "flagged_contradiction") in rels
    blob = str(g1)
    for secret in ("reliability", "red_herring", "\"level\""):
        assert secret not in blob


def test_graph_is_owner_scoped(ctx):
    c, _, _ = ctx
    a, b = signup(c, "ga@x.co"), signup(c, "gb@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=a).json()["session_id"]
    assert c.get(f"/sessions/{sid}/graph", headers=b).status_code == 404


# ---------------- read-only sharing hook ----------------
def test_share_link_is_read_only_and_revocable(ctx):
    c, _, _ = ctx
    h = signup(c, "sh@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h).json()["session_id"]
    share = c.post(f"/sessions/{sid}/share", headers=h).json()
    token = share["share_token"]
    r = c.get(f"/shared/{token}")                       # no auth header at all
    assert r.status_code == 200
    d = r.json()
    assert d["case"]["case_id"] == "CASE-014" and "report" not in d
    assert c.post(f"/shared/{token}/ask", json={"text": "x"}).status_code == 404  # no write surface
    assert c.delete(f"/sessions/{sid}/share", headers=h).status_code == 204
    assert c.get(f"/shared/{token}").status_code == 404


def test_share_link_includes_report_once_closed(ctx):
    c, _, _ = ctx
    h = signup(c, "sh2@x.co")
    sid = start_and_conclude(c, h, correct=True)
    token = c.post(f"/sessions/{sid}/share", headers=h).json()["share_token"]
    d = c.get(f"/shared/{token}").json()
    assert d["closed"] is True and d["report"]["status"] == "solved"


def test_only_owner_can_create_or_revoke_a_share(ctx):
    c, _, _ = ctx
    a, b = signup(c, "sa@x.co"), signup(c, "sb@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=a).json()["session_id"]
    assert c.post(f"/sessions/{sid}/share", headers=b).status_code == 404
    assert c.delete(f"/sessions/{sid}/share", headers=b).status_code == 404


def test_account_deletion_revokes_its_share_links(ctx):
    c, _, _ = ctx
    h = signup(c, "sd@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h).json()["session_id"]
    token = c.post(f"/sessions/{sid}/share", headers=h).json()["share_token"]
    c.delete("/privacy/account", headers=h)
    assert c.get(f"/shared/{token}").status_code == 404
