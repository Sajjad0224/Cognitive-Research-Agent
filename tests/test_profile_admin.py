"""Phase 7: cross-case skill profile, admin case authoring, analytics."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cra.app import create_app, load_cases_dir
from cra.auth import CONSENT_VERSION
from tests.test_api import PW, Clock, signup


@pytest.fixture
def admin_ctx(tmp_path):
    src = Path("cases")
    for f in src.glob("*.json"):
        shutil.copy(f, tmp_path / f.name)
    clock = Clock()
    app = create_app(load_cases_dir(tmp_path), clock=clock, admin_emails=frozenset(["admin@x.co"]),
                     cases_dir=tmp_path)
    return TestClient(app), clock, tmp_path


def new_case_body(case_id="CASE-TEST-01"):
    return {
        "case_id": case_id, "title": "Test Case", "domain": "test", "difficulty": "easy",
        "overview": "o", "objective": "obj", "time_limit_minutes": None,
        "hypotheses": [{"id": "T1", "level": "correct", "statement": "It was X",
                        "match_keywords": ["x", "was"]}],
        "evidence": [{"id": "T-01", "title": "Doc", "type": "document", "content": "c",
                     "reliability": 0.9, "relevance": 0.9, "directness": 0.9, "initial": True}],
        "contradictions": [], "required_evidence": [], "scoring_weights": {},
    }


# ---------------- admin auth gating ----------------
def test_admin_flag_set_only_for_configured_emails(admin_ctx):
    c, _, _ = admin_ctx
    admin = signup(c, "admin@x.co")
    plain = signup(c, "nobody@x.co")
    assert c.get("/me", headers=admin).json()["is_admin"] is True
    assert c.get("/me", headers=plain).json()["is_admin"] is False
    assert c.get("/admin/cases", headers=admin).status_code == 200
    assert c.get("/admin/cases", headers=plain).status_code == 403
    assert c.get("/admin/analytics", headers=plain).status_code == 403


# ---------------- admin case authoring ----------------
def test_admin_can_create_read_update_delete_case(admin_ctx):
    c, _, tmp_path = admin_ctx
    admin = signup(c, "admin@x.co")
    body = new_case_body()
    r = c.post("/admin/cases", json=body, headers=admin)
    assert r.status_code == 201 and r.json()["case_id"] == "CASE-TEST-01"
    assert (tmp_path / "CASE-TEST-01.json").exists()
    assert c.post("/admin/cases", json=body, headers=admin).status_code == 409       # duplicate
    ids = {x["case_id"] for x in c.get("/cases", headers=admin).json()}
    assert "CASE-TEST-01" in ids

    bad = dict(body); bad["hypotheses"] = [{"id": "T1", "level": "incorrect", "statement": "x",
                                            "match_keywords": ["x"]}]
    r = c.post("/admin/cases", json={**bad, "case_id": "CASE-BAD"}, headers=admin)
    assert r.status_code == 400 and "correct" in r.json()["detail"]

    body["title"] = "Renamed"
    assert c.put("/admin/cases/CASE-TEST-01", json=body, headers=admin).status_code == 200
    assert c.get("/admin/cases/CASE-TEST-01", headers=admin).json()["title"] == "Renamed"
    assert c.put("/admin/cases/WRONG-ID", json=body, headers=admin).status_code == 400

    # a user starts a session -> case can no longer be deleted
    user = signup(c, "u2@x.co")
    sid = c.post("/sessions", json={"case_id": "CASE-TEST-01"}, headers=user).json()["session_id"]
    assert c.delete("/admin/cases/CASE-TEST-01", headers=admin).status_code == 409
    r2 = c.put("/admin/cases/CASE-TEST-01", json=body, headers=admin)
    assert r2.status_code == 200 and "note" in r2.json()
    # still playable after the update
    assert c.get(f"/sessions/{sid}", headers=user).status_code == 200

    r3 = c.post("/admin/cases", json=new_case_body("CASE-TEST-02"), headers=admin)
    assert r3.status_code == 201
    assert c.delete("/admin/cases/CASE-TEST-02", headers=admin).status_code == 204
    assert not (tmp_path / "CASE-TEST-02.json").exists()
    assert c.delete("/admin/cases/NOPE", headers=admin).status_code == 404


def test_non_admin_cannot_author_cases(admin_ctx):
    c, _, _ = admin_ctx
    plain = signup(c, "p@x.co")
    assert c.post("/admin/cases", json=new_case_body(), headers=plain).status_code == 403


# ---------------- analytics ----------------
def test_admin_analytics_aggregates_across_users(admin_ctx):
    c, _, _ = admin_ctx
    admin = signup(c, "admin@x.co")
    a, b = signup(c, "a2@x.co"), signup(c, "b2@x.co")
    for h, correct in ((a, True), (b, False)):
        sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h).json()["session_id"]
        text = ("Ken intentionally deleted the data to hide errors" if correct
               else "The server migration accidentally deleted the data")
        c.post(f"/sessions/{sid}/conclusion", json={"text": text, "confidence": 0.7, "cited_evidence": []},
              headers=h)
    an = c.get("/admin/analytics", headers=admin).json()
    assert an["total_users"] >= 3 and an["total_sessions"] >= 2
    row = an["per_case"]["CASE-014"]
    assert row["attempts"] == 2 and row["completed"] == 2 and row["solve_rate"] == 0.5
    assert row["avg_final_score"] is not None


# ---------------- cross-case profile ----------------
def test_profile_aggregates_across_completed_cases(admin_ctx):
    c, _, _ = admin_ctx
    h = signup(c, "prof@x.co")
    empty = c.get("/profile", headers=h).json()
    assert empty["cases_completed"] == 0 and empty["skills"] == {}

    sid = c.post("/sessions", json={"case_id": "CASE-014"}, headers=h).json()["session_id"]
    c.post(f"/sessions/{sid}/conclusion",
          json={"text": "Ken intentionally deleted the data to hide errors", "confidence": 0.6,
                "cited_evidence": []}, headers=h)
    sid2 = c.post("/sessions", json={"case_id": "CASE-ADAPT-01"}, headers=h).json()["session_id"]
    c.post(f"/sessions/{sid2}/evidence/view", json={"evidence_id": "A-01"}, headers=h)

    prof = c.get("/profile", headers=h).json()
    assert prof["cases_attempted"] == 2 and prof["cases_completed"] == 1
    assert len(prof["history"]) == 1 and prof["history"][0]["case_id"] == "CASE-014"
    assert all(v["average"] is not None for v in prof["skills"].values())


def test_profile_is_per_user(admin_ctx):
    c, _, _ = admin_ctx
    a, b = signup(c, "pa@x.co"), signup(c, "pb@x.co")
    c.post("/sessions", json={"case_id": "CASE-014"}, headers=a)
    assert c.get("/profile", headers=b).json()["cases_attempted"] == 0
