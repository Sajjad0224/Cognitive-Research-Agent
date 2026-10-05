"""Local dev server:  python main.py   ->  http://127.0.0.1:8000/docs"""
import os
from pathlib import Path

import uvicorn

from cra.app import create_app, load_cases_dir
from cra.llm import from_env

HERE = Path(__file__).parent
CASES_DIR = HERE / "cases"
# from_env() returns None unless ANTHROPIC_API_KEY is set (and LLM_ENABLED != "false"),
# in which case the platform runs fully deterministic (see cra/llm.py, cra/llm_adapters.py).
# ADMIN_EMAILS: comma-separated list of emails to mark as admin at registration time.
admin_emails = frozenset(e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip())
app = create_app(load_cases_dir(CASES_DIR), event_db=str(HERE / "events.db"),
                 auth_db=str(HERE / "auth.db"), llm_client=from_env(),
                 admin_emails=admin_emails, cases_dir=CASES_DIR,
                 share_db=str(HERE / "shares.db"))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
