import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from cra.app import create_app, load_cases_dir
from cra.auth import CONSENT_VERSION

PW = "correct horse battery"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def ctx():
    clock = Clock()
    app = create_app(load_cases_dir("cases"), clock=clock)
    return TestClient(app), clock, app


def signup(client, email, pw=PW):
    r = client.post("/auth/register", json={"email": email, "password": pw, "consent_version": CONSENT_VERSION})
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_auth_rules(ctx):
    c, clock, app = ctx
    assert c.get("/consent").json()["version"] == CONSENT_VERSION
    bad = [
        {"email": "a@b.co", "password": PW, "consent_version": "old"},          # wrong consent
        {"email": "not-an-email", "password": PW, "consent_version": CONSENT_VERSION},
        {"email": "a@b.co", "password": "short", "consent_version": CONSENT_VERSION},
    ]
    for body in bad:
        assert c.post("/auth/register", json=body).status_code == 400
    assert c.post("/auth/register", json={"email": "a@b.co", "password": PW}).status_code == 422  # missing consent
    assert c.post("/auth/register", json={"email": "a@b.co", "password": PW, "consent_version": CONSENT_VERSION,
                                          "admin": True}).status_code == 422                        # extra field
    h = signup(c, "Ann@Example.com")
    assert c.post("/auth/register", json={"email": "ann@example.com", "password": PW,
                                          "consent_version": CONSENT_VERSION}).status_code == 409  # case-insensitive dup
    assert c.get("/me", headers=h).json()["email"] == "ann@example.com"
    assert c.post("/auth/login", json={"email": "ann@example.com", "password": "wrong-password"}).status_code == 401
    assert c.post("/auth/login", json={"email": "nobody@example.com", "password": PW}).status_code == 401
    assert app.state.auth.raw_password_hash("ann@example.com") != PW.encode()
    assert c.post("/auth/logout", headers=h).status_code == 204
    assert c.get("/me", headers=h).status_code == 401


def test_protected_endpoints_need_token(ctx):
    c, _, _ = ctx
    for method, url in (("get", "/cases"), ("post", "/sessions"), ("get", "/sessions"), ("get", "/sessions/S-x"),
                        ("get", "/sessions/S-x/report"), ("post", "/sessions/S-x/ask")):
        assert getattr(c, method)(url).status_code == 401
    assert c.get("/cases", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_token_expiry(ctx):
    c, clock, _ = ctx
    h = signup(c, "e@x.co")
    assert c.get("/me", headers=h).status_code == 200
    clock.now += timedelta(hours=13)
    assert c.get("/me", headers=h).status_code == 401


def test_full_investigation_over_http_and_no_leaks(ctx):
    c, _, _ = ctx
    h = signup(c, "u@x.co")
    cases = c.get("/cases", headers=h).json()
    by_id = {x["case_id"]: x for x in cases}
    assert "CASE-014" in by_id and "hypotheses" not in by_id["CASE-014"]
    r = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h)
    assert r.status_code == 201
    sid = r.json()["session_id"]
    assert {e["id"] for e in r.json()["evidence"]} == {"E-01", "E-02", "E-03"}
    assert c.post("/sessions", json={"case_id": "NOPE"}, headers=h).status_code == 404

    S = f"/sessions/{sid}"
    assert c.post(f"{S}/evidence/view", json={"evidence_id": "E-04"}, headers=h).status_code == 400  # undiscovered
    r = c.post(f"{S}/ask", json={"text": "Show me the audit log"}, headers=h)
    assert [e["id"] for e in r.json()["new_evidence"]] == ["E-04"]
    assert c.post(f"{S}/evidence/view", json={"evidence_id": "E-04"}, headers=h).status_code == 200
    assert c.post(f"{S}/evidence/relevance", json={"evidence_id": "E-02", "relevant": False}, headers=h).status_code == 204
    hid = c.post(f"{S}/hypotheses", json={"statement": "Ken intentionally deleted the data to hide errors",
                                          "confidence": 0.6}, headers=h).json()["hypothesis_id"]
    assert c.post(f"{S}/links", json={"hypothesis_id": hid, "evidence_id": "E-04", "relation": "supports"},
                  headers=h).status_code == 201
    assert c.post(f"{S}/links", json={"hypothesis_id": hid, "evidence_id": "E-04", "relation": "supports"},
                  headers=h).status_code == 400                                                    # duplicate
    assert c.post(f"{S}/links", json={"hypothesis_id": hid, "evidence_id": "E-04", "relation": "maybe"},
                  headers=h).status_code == 422
    assert c.post(f"{S}/contradictions", json={"evidence_a": "E-02", "evidence_b": "E-04"}, headers=h).status_code == 201
    assert c.patch(f"{S}/hypotheses/{hid}", json={"confidence": 0.8}, headers=h).status_code == 204
    assert c.patch(f"{S}/hypotheses/{hid}", json={}, headers=h).status_code == 400
    assert c.post(f"{S}/notes", json={"text": "check badge log"}, headers=h).status_code == 201

    ws = c.get(S, headers=h).json()
    assert ws["hypotheses"][0]["confidence"] == 0.8 and ws["notes"] == ["check badge log"]
    blob = json.dumps(ws)
    for secret in ("reliability", "red_herring", "match_keywords", "supports\": [", "\"level\""):
        assert secret not in blob

    assert c.get(f"{S}/report", headers=h).status_code == 409                                       # not yet
    r = c.post(f"{S}/conclusion", json={"text": "Ken intentionally deleted the data to hide errors",
                                        "confidence": 0.8, "cited_evidence": ["E-04"]}, headers=h)
    assert r.status_code == 201
    assert c.post(f"{S}/ask", json={"text": "more?"}, headers=h).status_code == 400                 # closed
    rep = c.get(f"{S}/report", headers=h).json()
    assert rep["status"] == "solved" and rep["case_id"] == "CASE-014"
    assert c.get("/sessions", headers=h).json()[0]["closed"] is True


def test_sessions_are_private_per_user(ctx):
    c, _, _ = ctx
    a, b = signup(c, "a@x.co"), signup(c, "b@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=a).json()["session_id"]
    S = f"/sessions/{sid}"
    probes = [("get", S, None), ("get", f"{S}/report", None),
              ("post", f"{S}/ask", {"text": "audit log"}),
              ("post", f"{S}/evidence/view", {"evidence_id": "E-01"}),
              ("post", f"{S}/hypotheses", {"statement": "x"}),
              ("post", f"{S}/conclusion", {"text": "x", "confidence": 0.5})]
    for method, url, body in probes:
        r = getattr(c, method)(url, headers=b, **({"json": body} if body else {}))
        assert r.status_code == 404, (url, r.status_code)
    assert c.get("/sessions", headers=b).json() == []
    assert c.get(S, headers=a).status_code == 200                       # owner unaffected
    assert c.get("/sessions/S-doesnotexist", headers=a).status_code == 404


def test_validation_limits(ctx):
    c, _, _ = ctx
    h = signup(c, "v@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h).json()["session_id"]
    S = f"/sessions/{sid}"
    assert c.post(f"{S}/ask", json={"text": ""}, headers=h).status_code == 422
    assert c.post(f"{S}/ask", json={"text": "x" * 5001}, headers=h).status_code == 422
    assert c.post(f"{S}/hypotheses", json={"statement": "x", "confidence": 2}, headers=h).status_code == 422
    assert c.post(f"{S}/conclusion", json={"text": "x", "confidence": 0.5, "cited_evidence": [1]},
                  headers=h).status_code == 400
    assert c.post(f"{S}/conclusion", json={"text": "x", "confidence": 0.5, "cited_evidence": ["E-04"]},
                  headers=h).status_code == 400                        # undiscovered evidence


def test_ui_is_served_and_public(ctx):
    c, _, _ = ctx
    r = c.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Cognitive Research Agent" in r.text and "/auth/login" in r.text


def test_report_gets_llm_narrative_when_client_configured():
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient

    from cra.app import create_app, load_cases_dir
    from cra.auth import CONSENT_VERSION

    class FakeClient:
        def complete(self, system, user, max_tokens=300):
            return "The investigator built a coherent, well-supported case."

    clock = Clock()
    app = create_app(load_cases_dir("cases"), clock=clock, llm_client=FakeClient())
    c = TestClient(app)
    h = signup(c, "n@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h).json()["session_id"]
    c.post(f"/sessions/{sid}/conclusion",
          json={"text": "Ken intentionally deleted the data to hide errors", "confidence": 0.9,
                "cited_evidence": []}, headers=h)
    rep = c.get(f"/sessions/{sid}/report", headers=h).json()
    assert rep["reasoning_analysis"] == "The investigator built a coherent, well-supported case."
    assert rep["performance"]["outcome"] == 100.0     # scoring is unaffected by the LLM


def test_report_has_no_narrative_key_without_llm_client(ctx):
    c, _, _ = ctx
    h = signup(c, "m@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h).json()["session_id"]
    c.post(f"/sessions/{sid}/conclusion",
          json={"text": "Ken intentionally deleted the data to hide errors", "confidence": 0.9,
                "cited_evidence": []}, headers=h)
    rep = c.get(f"/sessions/{sid}/report", headers=h).json()
    assert "reasoning_analysis" not in rep
