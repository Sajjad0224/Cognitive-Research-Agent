# Cognitive Research Agent

AI-powered research case-solving and cognitive process assessment platform.

## What is implemented

- Case library and admin authoring foundation
- Structured evidence with visibility, reliability, relevance and directness metadata
- Knowledge graph entities and relationships
- Evidence relationships and unlock rules
- Multiple accepted/required solution hypotheses
- Investigation sessions with immutable, sequential events
- Evidence access tracking
- User-created hypotheses
- Controlled investigation agent with deterministic fallback
- Optional OpenAI-powered case agent (API key required)
- Deterministic multi-factor evaluation engine
- Explainable outcome/reasoning/process scores
- 12-skill cognitive profile
- Longitudinal skill profiles and history
- Responsive Bootstrap-inspired vanilla HTML/CSS/JS interface
- Django REST API
- Admin case authoring
- Demo case seed command
- SQLite development mode and MySQL production mode

## Architecture

`Case Truth -> Evidence/Graph -> Investigation Session -> Immutable Events -> Feature Extraction -> Deterministic Evaluation -> Explainable Report -> Longitudinal Skill Profile`

The LLM is intentionally not the final scoring authority.

## Local setup

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env  # macOS/Linux
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo
python manage.py runserver
```

Create a Django superuser before running `seed_demo`; the command never stores or creates a default password.

## MySQL

Set `DB_ENGINE=mysql` and the MySQL connection variables in `.env` before running migrations.

## OpenAI

Set `OPENAI_API_KEY` to enable the optional LLM investigation agent. Without it, the deterministic evidence-retrieval agent remains functional.

## API

- `GET /api/cases/`
- `GET /api/cases/<uuid>/`
- `GET/POST /api/investigations/`
- `GET/POST /api/investigations/<session>/events/`
- `POST /api/investigations/<session>/agent/`
- `POST /api/investigations/<session>/evidence/<evidence>/`
- `POST /api/investigations/<session>/hypotheses/`
- `POST /api/investigations/<session>/submit/`

## Evaluation model

The platform separates:

1. **Outcome** — whether the final conclusion matches an accepted solution space.
2. **Reasoning** — evidence evaluation, hypothesis testing, analytical and logical reasoning.
3. **Process** — evidence coverage, research behavior and configurable case-specific skill weights.

Scores are computed from observable investigation events. The LLM may classify or explain language but does not arbitrarily choose the final score.

## Phase map

1. Foundation
2. Case engine
3. Investigation workspace
4. AI investigation agent
5. Behavior analysis
6. Evaluation engine
7. Explainable reports
8. Longitudinal skill profile
9. Adaptive case foundation
10. Production hardening foundation
