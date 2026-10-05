# Cognitive Research Agent — build plan & status

Setup: `pip install -r requirements.txt`. Tests: `python -m pytest -q` (73, all offline/no network). Demo: `python demo.py`.
API: `python main.py` then open http://127.0.0.1:8000/ (UI) or /docs (API) (Python 3.10+; core engine is stdlib-only).

## Phases
| # | Phase | Status |
|---|-------|--------|
| 1 | Foundation: case schema + strict validation, immutable SQLite event store, event-sourced state | DONE |
| 2 | Investigation engine: gated evidence discovery, intent classification, hypotheses, links, contradictions, conclusion, leak-proof public views | DONE |
| 3 | Evaluation engine: 19 deterministic features, per-case skill weights, missing-data renormalisation, guess detection, explainable report | DONE |
| 4 | REST API (FastAPI) + scrypt auth + bearer tokens + versioned consent, sessions private to their owner, per-session action locking | DONE |
| 5 | Web UI (single file, no build step, served at `/`): auth+consent, case list, evidence panel, chat, hypotheses/links, contradiction flags, notes, conclusion, report | DONE |
| 6 | LLM adapters: Case Agent (Responder), Reasoning Analyzer (matcher + intent), Feedback Generator (narrative only); scoring stays deterministic | DONE |
| 7 | Cross-case user skill profile, adaptive difficulty, admin case authoring UI, analytics | |
| 8 | Privacy (export/delete), login hardening + security headers, knowledge-graph view, read-only session sharing hook | DONE |

## Design rules enforced in code
- Events are append-only (SQLite triggers block UPDATE/DELETE); all scores are recomputed from events.
- `evaluate(case, events)` is a pure function: same events -> identical report.
- Users only ever see `id/title/type/content`; reliability, relevance, supports/contradicts, levels stay hidden.
- Indicators with no data are excluded and weights renormalised (never scored as fake zeros); each skill reports `data_coverage`.
- Outcome and process are scored separately; a correct guess is flagged "Correct outcome, weak evidentiary reasoning."
- Ambiguous free-text conclusions are never credited (matcher returns None on ties).

## Known limits (deliberate, for later phases)
- Free-text -> case-hypothesis mapping is keyword-based (`cra/matcher.py`); swap in an LLM matcher in Phase 6.
- Leadership / communication / time management / strategic thinking are not scored yet (no reliable signals in MVP).
- Session ownership checks belong to the API layer (Phase 4).

## API notes (Phase 4)
- Register requires the current consent version (`GET /consent`); login returns an opaque bearer token (12h expiry, stored hashed).
- Another user's session returns 404 (existence is not revealed). Report is only available after a conclusion (409 before).
- Known gaps for later phases: no rate limiting / login lockout, no password reset, no admin roles, no data export/delete yet (Phase 8), coarse single engine lock (fine for one process; use per-session or DB locking when scaling out).
- UI notes: all dynamic text is HTML-escaped; token kept in sessionStorage; no visual timeline/graph yet (planned with Phase 7 polish).

## LLM adapters (Phase 6)
Off by default — the platform is fully deterministic without any API key. To enable:
`export ANTHROPIC_API_KEY=...` (optionally `ANTHROPIC_MODEL=...`), then run `python main.py`.
Set `LLM_ENABLED=false` to force the deterministic path even with a key present.

- `cra/llm.py` — thin Anthropic Messages API client (`from_env()` returns None without a key).
- `cra/llm_adapters.py`:
  - `LLMResponder` — Case Agent; answers questions from public evidence only, never the hidden
    solution; falls back to the fixed-template responder on any error.
  - `LLMMatcher` — maps a free-text hypothesis/conclusion to an *author-defined* hypothesis id;
    rejects hallucinated ids and low-confidence matches (falls back to the keyword matcher).
  - `LLMIntentClassifier` — same fallback pattern for question intent labels.
  - `add_reasoning_narrative(report, client)` — adds `report["reasoning_analysis"]` prose built
    only from already-computed numbers; returns the input unchanged on failure and never edits
    `performance`/`skills`. Tested to prove the LLM cannot alter a single score.
- All adapter tests (`tests/test_llm_adapters.py`) use an in-process fake client — no network
  calls, no API cost, fully deterministic in CI.

## Phase 7 notes
- **Cross-case skill profile** (`cra/profile.py`, `GET /profile`): recomputed on demand from a
  user's own completed sessions — no separate mutable profile table, so it can't drift out of
  sync. Per-skill average + a simple first-half-vs-second-half trend (`improving` /
  `needs_focus` / `steady`, needs 2+ scored cases). `behavioral_patterns` are a few conservative,
  clearly-labelled observations (e.g. repeated questions), never a trait or diagnosis.
- **Adaptive difficulty** (`cra/engine.py::_adaptive_ready`, case field `evidence[].adaptive`):
  an evidence item can declare `{"trigger": "stall", "after": N}` (extra contextual clue after N
  consecutive unproductive questions) or `{"trigger": "strong", "after": N}` (extra
  contradicting "twist" evidence once the investigator has N+ evidence links across 2+
  hypotheses). Checked inside `ask()`, using only counts already in the event-sourced state — no
  new event types, and the case's hypothesis levels / required evidence are never touched, so
  correctness criteria can't shift. See `cases/adaptive_demo_case.json` for a worked example;
  the original `CASE-014` is untouched by this feature.
- **Admin case authoring** (`POST/PUT/GET/DELETE /admin/cases`, admin-only via `ADMIN_EMAILS`):
  create/update validate through the same `parse_case` as startup, so a bad case is rejected the
  same way either time. Persisted to `cases_dir` as JSON when one is configured. A case with any
  existing session cannot be deleted (409); it *can* be updated in place — later report requests
  for those sessions score against the newer version, and the response says so.
- **Analytics dashboard** (`GET /admin/analytics`, admin-only): per-case attempts/completions,
  solve rate, average final score and average time-to-conclusion, computed by replaying every
  session's events on request (fine at this scale; would need caching for a very large event log).
- Admin bootstrap: `ADMIN_EMAILS=you@x.co,other@x.co` (see `main.py`) marks matching accounts as
  admin at *registration* time only; promoting an existing account needs a direct DB edit for now.
- UI: a top-nav (Cases / My profile / Admin) was added; the admin case editor is a plain
  paste-JSON textarea rather than a structured form — deliberately minimal for this phase.

## Phase 8 notes
- **Privacy — export** (`GET /privacy/export`): returns everything this platform holds about the
  caller — account record (no password hash), every event they ever produced across all
  sessions, and the reports for their completed cases. `GET /privacy/notice` (public, no auth)
  states in plain language what's tracked.
- **Privacy — account deletion** (`DELETE /privacy/account`): irreversibly deletes the account
  row, all its bearer tokens, every share link it created, and all of its investigation events.
  Events are normally append-only — `cra/events.py` SQLite triggers reject every UPDATE/DELETE —
  but `EventStore.erase_user()` is one deliberate, narrowly-scoped exception to that for this one
  purpose: it drops the delete-blocking trigger, deletes only that user's rows, and restores the
  trigger in the same transaction, so every other user's history stays exactly as immutable as
  before. There is no other code path to it; it is never used to edit or "fix" a score.
- **Login hardening** (`cra/auth.py`): 5 failed logins for an email lock it for 15 minutes
  (in-memory, reset on process restart — a stated trade-off for a single-process deployment, not
  a claim of complete brute-force protection); a successful login clears the counter. Password
  checks still take one hash either way (existing vs. unknown email, right vs. wrong password),
  so a lockout can't be used to enumerate which emails are registered.
- **Security headers** (`cra/app.py` middleware): every response gets `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a `Content-Security-Policy`
  scoped to the single-file UI's own inline script/style and same-origin `fetch()` calls. No HSTS
  here — that's a TLS-terminator/deployment concern, not something to assert over plain HTTP.
- **Knowledge-graph view** (`cra/graph.py`, `GET /sessions/{id}/graph`): a nodes/edges reshaping
  of data the investigator already has in the workspace response — discovered evidence, their
  own hypotheses, their own evidence links and contradiction flags. Adds no new information and
  leaks none of a case's hidden metadata; it's a different shape over the same already-public
  facts, for a future graph-visualisation UI.
- **Read-only session sharing** (`cra/sharing.py`, `POST/DELETE /sessions/{id}/share`,
  public `GET /shared/{token}`): a small, concrete step toward the "multiplayer / team-based
  investigations" future feature, without building real-time collaboration. An owner mints an
  unguessable token; anyone holding it gets a read-only snapshot (evidence discovered so far,
  hypotheses, links, notes, and the report once closed) with no account needed. There is no
  write surface on the shared view — every mutating action still requires the owner's own bearer
  token via `owned()`. Deleting an account revokes all of its share links.
- UI: added a "Privacy" nav page (export download, delete-account button with confirmation) and
  a "Share" button on the investigation header that copies a read-only link to the clipboard.
