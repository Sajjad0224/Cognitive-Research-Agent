"""FastAPI layer: authentication, consent, per-user session ownership, investigation actions."""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Literal, Optional

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .auth import CONSENT_TEXT, CONSENT_VERSION, AuthError, AuthStore
from .case import Case, CaseValidationError, load_case, parse_case
from .engine import InvestigationEngine, InvestigationError, NotFoundError
from .events import EventStore
from .graph import build_graph
from .llm import LLMClient
from .llm_adapters import LLMIntentClassifier, LLMMatcher, LLMResponder, add_reasoning_narrative
from .matcher import KeywordMatcher
from .profile import build_user_profile
from .report import build_report
from .sharing import ShareStore
from .state import reduce_events

_bearer = HTTPBearer(auto_error=False)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterIn(_Strict):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    consent_version: str = Field(max_length=64)


class LoginIn(_Strict):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class SessionIn(_Strict):
    case_id: str = Field(min_length=1, max_length=100)


class EvidenceRefIn(_Strict):
    evidence_id: str = Field(min_length=1, max_length=50)


class RelevanceIn(EvidenceRefIn):
    relevant: bool


class QuestionIn(_Strict):
    text: str = Field(min_length=1, max_length=5000)


class HypothesisIn(_Strict):
    statement: str = Field(min_length=1, max_length=5000)
    confidence: float = Field(default=0.5, ge=0, le=1)


class HypothesisUpdateIn(_Strict):
    statement: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    status: Optional[Literal["active", "abandoned", "supported", "refuted"]] = None


class LinkIn(EvidenceRefIn):
    hypothesis_id: str = Field(min_length=1, max_length=50)
    relation: Literal["supports", "contradicts"]


class ContradictionIn(_Strict):
    evidence_a: str = Field(min_length=1, max_length=50)
    evidence_b: str = Field(min_length=1, max_length=50)


class NoteIn(_Strict):
    text: str = Field(min_length=1, max_length=5000)


class ConclusionIn(_Strict):
    text: str = Field(min_length=1, max_length=5000)
    confidence: float = Field(ge=0, le=1)
    cited_evidence: list = Field(default_factory=list, max_length=100)


def load_cases_dir(path) -> dict:
    cases = {}
    for f in sorted(Path(path).glob("*.json")):
        c = load_case(f)
        if c.case_id in cases:
            raise ValueError(f"duplicate case_id {c.case_id} in {f}")
        cases[c.case_id] = c
    return cases


def create_app(cases: dict, event_db: str = ":memory:", auth_db: str = ":memory:",
               clock: Optional[Callable] = None, llm_client: Optional[LLMClient] = None,
               admin_emails: frozenset = frozenset(), cases_dir: Optional[Path] = None,
               share_db: str = ":memory:") -> FastAPI:
    """llm_client is optional (see cra/llm.py::from_env). Without one, the Case Agent uses fixed
    templates and free-text matching is keyword-based — fully deterministic, no external calls.
    With one, natural-language quality improves; scoring in cra/scoring.py never changes either way.

    admin_emails marks accounts with that (normalised) email as admin at registration time. If
    cases_dir is given, admin-created/updated cases are persisted there as JSON so they survive
    a restart; without it, admin case changes are in-memory only for this process's lifetime.
    """
    app = FastAPI(title="Cognitive Research Agent", version="0.5.0")
    store = EventStore(event_db, clock=clock)
    auth = AuthStore(auth_db, clock=clock, admin_emails=admin_emails)
    shares = ShareStore(share_db, clock=clock)
    responder = LLMResponder(llm_client) if llm_client else None
    intent_fn = LLMIntentClassifier(llm_client) if llm_client else None
    matcher = LLMMatcher(llm_client) if llm_client else KeywordMatcher()
    engine = InvestigationEngine(store, cases, responder=responder, intent_classifier=intent_fn)
    raw_cases: dict = {}
    if cases_dir is not None:
        for cid in cases:
            f = Path(cases_dir) / f"{cid}.json"
            if f.exists():
                try:
                    raw_cases[cid] = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass  # admin GET simply won't show this case's raw authoring data
    app.state.store, app.state.auth, app.state.engine, app.state.matcher = store, auth, engine, matcher
    app.state.raw_cases, app.state.cases_dir, app.state.shares = raw_cases, cases_dir, shares

    # ---- hardening (Phase 8): conservative security headers on every response ----------------
    # Chosen to not break the single-file UI (cra/static/index.html), which uses an inline
    # <script>/<style> and fetch() back to this same origin — so CSP allows 'self' + inline for
    # script/style/connect, and nothing else. No HSTS here: that is a deployment/TLS-terminator
    # concern, not something this dev-oriented app server should assert on plain HTTP.
    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
        return response

    def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> dict:
        user = auth.user_for_token(creds.credentials) if creds else None
        if user is None:
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
        return user

    def owned(session_id: str, user: dict):
        """Return the session state only if it belongs to the caller; otherwise behave as 'not found'."""
        try:
            st = engine.state(session_id)
        except NotFoundError:
            raise HTTPException(404, "session not found") from None
        if st.user_id != user["id"]:
            raise HTTPException(404, "session not found")
        return st

    def require_admin(user: dict = Depends(current_user)) -> dict:
        if not user.get("is_admin"):
            raise HTTPException(403, "admin access required")
        return user

    def act(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from None
        except InvestigationError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(Path(__file__).parent / "static" / "index.html", media_type="text/html",
                            headers={"Cache-Control": "no-cache"})

    # ---- auth -----------------------------------------------------------
    @app.get("/consent")
    def consent():
        return {"version": CONSENT_VERSION, "text": CONSENT_TEXT}

    @app.post("/auth/register", status_code=201)
    def register(body: RegisterIn):
        try:
            return auth.register(body.email, body.password, body.consent_version)
        except AuthError as exc:
            raise HTTPException(409 if "already" in str(exc) else 400, str(exc)) from None

    @app.post("/auth/login")
    def login(body: LoginIn):
        try:
            token, user = auth.login(body.email, body.password)
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from None
        return {"access_token": token, "token_type": "bearer", "user": user}

    @app.post("/auth/logout", status_code=204)
    def logout(creds: HTTPAuthorizationCredentials = Depends(_bearer), user: dict = Depends(current_user)):
        auth.logout(creds.credentials)

    @app.get("/me")
    def me(user: dict = Depends(current_user)):
        return user

    # ---- cases & sessions ---------------------------------------------------
    @app.get("/cases")
    def list_cases(user: dict = Depends(current_user)):
        return [c.briefing() for c in cases.values()]

    @app.post("/sessions", status_code=201)
    def start(body: SessionIn, user: dict = Depends(current_user)):
        sid = act(engine.start_session, body.case_id, user["id"])
        return {"session_id": sid, **engine.briefing(sid)}

    @app.get("/sessions")
    def my_sessions(user: dict = Depends(current_user)):
        out = []
        for sid in store.sessions_for_user(user["id"]):
            st = engine.state(sid)
            out.append({"session_id": sid, "case_id": st.case_id, "started_at": st.started_at,
                        "closed": st.closed})
        return out

    @app.get("/sessions/{session_id}")
    def workspace(session_id: str, user: dict = Depends(current_user)):
        st = owned(session_id, user)
        case = cases[st.case_id]
        return {
            "session_id": session_id, "case": case.briefing(), "closed": st.closed,
            "evidence": [case.evidence[e].public() for e in st.discovered],
            "viewed": list(st.viewed),
            "relevance_marks": {e: m["relevant"] for e, m in st.marks.items()},
            "hypotheses": [{"id": h["id"], "statement": h["statement"], "confidence": h["confidence"],
                            "status": h["status"]} for h in st.hypotheses.values()],
            "links": [{"hypothesis_id": l["hypothesis_id"], "evidence_id": l["evidence_id"],
                       "relation": l["relation"]} for l in st.links],
            "contradictions_flagged": [sorted(f["pair"]) for f in st.flags],
            "notes": [n["text"] for n in st.notes],
            "questions": [{"text": q["text"], "intent": q["intent"]} for q in st.questions],
        }

    # ---- investigation actions ---------------------------------------------
    @app.post("/sessions/{session_id}/evidence/view")
    def view(session_id: str, body: EvidenceRefIn, user: dict = Depends(current_user)):
        owned(session_id, user)
        return act(engine.view_evidence, session_id, body.evidence_id)

    @app.post("/sessions/{session_id}/evidence/relevance", status_code=204)
    def relevance(session_id: str, body: RelevanceIn, user: dict = Depends(current_user)):
        owned(session_id, user)
        act(engine.mark_relevance, session_id, body.evidence_id, body.relevant)

    @app.post("/sessions/{session_id}/ask")
    def ask(session_id: str, body: QuestionIn, user: dict = Depends(current_user)):
        owned(session_id, user)
        return act(engine.ask, session_id, body.text)

    @app.post("/sessions/{session_id}/hypotheses", status_code=201)
    def new_hypothesis(session_id: str, body: HypothesisIn, user: dict = Depends(current_user)):
        owned(session_id, user)
        return {"hypothesis_id": act(engine.create_hypothesis, session_id, body.statement, body.confidence)}

    @app.patch("/sessions/{session_id}/hypotheses/{hypothesis_id}", status_code=204)
    def edit_hypothesis(session_id: str, hypothesis_id: str, body: HypothesisUpdateIn,
                        user: dict = Depends(current_user)):
        owned(session_id, user)
        act(engine.update_hypothesis, session_id, hypothesis_id, statement=body.statement,
            confidence=body.confidence, status=body.status)

    @app.post("/sessions/{session_id}/links", status_code=201)
    def link(session_id: str, body: LinkIn, user: dict = Depends(current_user)):
        owned(session_id, user)
        act(engine.link_evidence, session_id, body.hypothesis_id, body.evidence_id, body.relation)
        return {"ok": True}

    @app.post("/sessions/{session_id}/contradictions", status_code=201)
    def contradiction(session_id: str, body: ContradictionIn, user: dict = Depends(current_user)):
        owned(session_id, user)
        act(engine.flag_contradiction, session_id, body.evidence_a, body.evidence_b)
        return {"ok": True}

    @app.post("/sessions/{session_id}/notes", status_code=201)
    def note(session_id: str, body: NoteIn, user: dict = Depends(current_user)):
        owned(session_id, user)
        act(engine.add_note, session_id, body.text)
        return {"ok": True}

    @app.post("/sessions/{session_id}/conclusion", status_code=201)
    def conclude(session_id: str, body: ConclusionIn, user: dict = Depends(current_user)):
        owned(session_id, user)
        act(engine.submit_conclusion, session_id, body.text, body.confidence, body.cited_evidence)
        return {"ok": True, "report_url": f"/sessions/{session_id}/report"}

    @app.get("/sessions/{session_id}/report")
    def report(session_id: str, user: dict = Depends(current_user)):
        st = owned(session_id, user)
        if not st.closed:
            raise HTTPException(409, "the report is available after you submit a conclusion")
        rep = build_report(cases[st.case_id], store.read(session_id), matcher=matcher)
        return add_reasoning_narrative(rep, llm_client) if llm_client else rep

    # ---- cross-case skill profile (Phase 7) --------------------------------
    @app.get("/profile")
    def profile(user: dict = Depends(current_user)):
        return build_user_profile(cases, store, user["id"], matcher)

    # ---- knowledge graph view of a session (Phase 8) -----------------------
    @app.get("/sessions/{session_id}/graph")
    def graph(session_id: str, user: dict = Depends(current_user)):
        st = owned(session_id, user)
        return build_graph(cases[st.case_id], st)

    # ---- read-only sharing hook (Phase 8) -----------------------------------
    @app.post("/sessions/{session_id}/share", status_code=201)
    def create_share(session_id: str, user: dict = Depends(current_user)):
        owned(session_id, user)
        token = shares.create(session_id, user["id"])
        return {"share_token": token, "share_url": f"/shared/{token}"}

    @app.delete("/sessions/{session_id}/share", status_code=204)
    def revoke_shares(session_id: str, user: dict = Depends(current_user)):
        owned(session_id, user)
        shares.revoke_all_for_session(session_id)

    @app.get("/shared/{token}")
    def view_shared(token: str):
        """Public (no bearer token needed): anyone holding this unguessable share link gets a
        read-only snapshot of the session. There is no write path here — mutating actions all go
        through /sessions/{id}/* and still require the owner's own auth (see owned() above)."""
        session_id = shares.session_for_token(token)
        if session_id is None:
            raise HTTPException(404, "share link not found or revoked")
        try:
            st = engine.state(session_id)
        except NotFoundError:
            raise HTTPException(404, "share link not found or revoked") from None
        case = cases[st.case_id]
        out = {
            "session_id": session_id, "case": case.briefing(), "closed": st.closed,
            "evidence": [case.evidence[e].public() for e in st.discovered],
            "hypotheses": [{"id": h["id"], "statement": h["statement"], "confidence": h["confidence"],
                            "status": h["status"]} for h in st.hypotheses.values()],
            "links": [{"hypothesis_id": l["hypothesis_id"], "evidence_id": l["evidence_id"],
                       "relation": l["relation"]} for l in st.links],
            "contradictions_flagged": [sorted(f["pair"]) for f in st.flags],
            "notes": [n["text"] for n in st.notes],
        }
        if st.closed:
            out["report"] = build_report(case, store.read(session_id), matcher=matcher)
        return out

    # ---- privacy: export & account deletion (Phase 8) -----------------------
    @app.get("/privacy/notice")
    def privacy_notice():
        return {"consent_version": CONSENT_VERSION, "consent_text": CONSENT_TEXT,
                "what_is_tracked": "Account email and consent record; every investigation action "
                "you take in a case (questions, evidence opened, hypotheses, links, notes, "
                "conclusions) with a timestamp; nothing from case authoring or other users.",
                "export_endpoint": "/privacy/export", "delete_endpoint": "/privacy/account"}

    @app.get("/privacy/export")
    def privacy_export(user: dict = Depends(current_user)):
        account = auth.get_user(user["id"])
        events = [{"event_id": e.event_id, "session_id": e.session_id, "case_id": e.case_id,
                  "ts": e.ts, "event_type": e.event_type, "payload": e.payload}
                 for e in store.export_for_user(user["id"])]
        reports = []
        for sid in store.sessions_for_user(user["id"]):
            st = reduce_events(store.read(sid))
            if st.closed and st.case_id in cases:
                reports.append(build_report(cases[st.case_id], store.read(sid), matcher=matcher))
        return {"account": account, "events": events, "reports": reports,
                "notice": "This is the complete data this platform holds about your account and "
                "investigation activity, exported at your request."}

    @app.delete("/privacy/account", status_code=204)
    def privacy_delete_account(creds: HTTPAuthorizationCredentials = Depends(_bearer),
                               user: dict = Depends(current_user)):
        """Irreversibly erases this account: the account row, all its bearer tokens, all of its
        investigation events (via EventStore.erase_user — see that method's docstring for why
        this is safe despite events normally being immutable), and any share links it created.
        Other users' data, including other users' sessions for the same case, is untouched."""
        for sid in store.sessions_for_user(user["id"]):
            shares.revoke_all_for_session(sid)
        store.erase_user(user["id"])
        auth.delete_user(user["id"])

    # ---- admin: case authoring (Phase 7) ------------------------------------
    def _persist(raw: dict) -> None:
        if app.state.cases_dir is not None:
            path = Path(app.state.cases_dir) / f"{raw['case_id']}.json"
            path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    @app.get("/admin/cases")
    def admin_list_cases(user: dict = Depends(require_admin)):
        return list(app.state.raw_cases.values())

    @app.get("/admin/cases/{case_id}")
    def admin_get_case(case_id: str, user: dict = Depends(require_admin)):
        if case_id not in app.state.raw_cases:
            raise HTTPException(404, "case not found")
        return app.state.raw_cases[case_id]

    @app.post("/admin/cases", status_code=201)
    def admin_create_case(body: dict = Body(...), user: dict = Depends(require_admin)):
        raw = body
        if raw.get("case_id") in cases:
            raise HTTPException(409, f"case_id {raw.get('case_id')!r} already exists; use PUT to update")
        try:
            parsed = parse_case(raw)
        except CaseValidationError as exc:
            raise HTTPException(400, str(exc)) from None
        cases[parsed.case_id] = parsed
        app.state.raw_cases[parsed.case_id] = raw
        engine.add_case(parsed)
        _persist(raw)
        return parsed.briefing()

    @app.put("/admin/cases/{case_id}")
    def admin_update_case(case_id: str, body: dict = Body(...), user: dict = Depends(require_admin)):
        raw = body
        if raw.get("case_id") != case_id:
            raise HTTPException(400, "body case_id must match the URL")
        try:
            parsed = parse_case(raw)
        except CaseValidationError as exc:
            raise HTTPException(400, str(exc)) from None
        cases[case_id] = parsed
        app.state.raw_cases[case_id] = raw
        engine.add_case(parsed)
        _persist(raw)
        note = ("Note: existing sessions for this case are re-scored against the updated case "
                "the next time their report is requested.") if store.has_sessions_for_case(case_id) else None
        out = parsed.briefing()
        if note:
            out["note"] = note
        return out

    @app.delete("/admin/cases/{case_id}", status_code=204)
    def admin_delete_case(case_id: str, user: dict = Depends(require_admin)):
        if case_id not in cases:
            raise HTTPException(404, "case not found")
        if store.has_sessions_for_case(case_id):
            raise HTTPException(409, "cannot delete a case that already has investigation sessions")
        del cases[case_id]
        app.state.raw_cases.pop(case_id, None)
        engine.remove_case(case_id)
        if app.state.cases_dir is not None:
            (Path(app.state.cases_dir) / f"{case_id}.json").unlink(missing_ok=True)

    # ---- admin: analytics dashboard (Phase 7) -------------------------------
    @app.get("/admin/analytics")
    def admin_analytics(user: dict = Depends(require_admin)):
        per_case: dict = {}
        for s in store.all_sessions():
            cid = s["case_id"]
            if cid not in cases:
                continue
            b = per_case.setdefault(cid, {"title": cases[cid].title, "attempts": 0, "completed": 0,
                                          "solved": 0, "finals": [], "times": []})
            b["attempts"] += 1
            events = store.read(s["session_id"])
            st = reduce_events(events)
            if not st.closed:
                continue
            b["completed"] += 1
            rep = build_report(cases[cid], events, matcher)
            if rep["status"] == "solved":
                b["solved"] += 1
            if rep["performance"]["final"] is not None:
                b["finals"].append(rep["performance"]["final"])
            tt = rep["behavior"]["time_to_conclusion_s"]
            if tt is not None:
                b["times"].append(tt)
        result: dict[str, Any] = {}
        for cid, b in per_case.items():
            result[cid] = {
                "title": b["title"], "attempts": b["attempts"], "completed": b["completed"],
                "solve_rate": round(b["solved"] / b["completed"], 3) if b["completed"] else None,
                "avg_final_score": round(mean(b["finals"]), 1) if b["finals"] else None,
                "avg_time_to_conclusion_s": round(mean(b["times"]), 1) if b["times"] else None,
            }
        return {"total_users": auth.user_count(), "total_sessions": len(store.all_sessions()),
                "per_case": result}

    return app
