"""Runs a strong investigator and a pure guesser through CASE-014 and prints both reports."""
from cra.report import build_report, render_text
from tests.scenarios import guesser, make_engine, strong_user

eng, case, store = make_engine()
for label, run in (("STRONG INVESTIGATOR", strong_user), ("PURE GUESSER", guesser)):
    sid = run(eng)
    print(f"\n######## {label} ########\n")
    print(render_text(build_report(case, store.read(sid))))
