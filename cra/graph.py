"""Knowledge-graph view of an investigation (spec section 5), queried on demand rather than
stored. The graph is derived ENTIRELY from data already visible to the investigator in the
workspace response (app.py::workspace): discovered evidence and the hypotheses/links/
contradiction-flags they themselves created. It adds no new information and never exposes a
case's hidden metadata (reliability, true supports/contradicts, hypothesis levels) — it is a
different shape over the same already-public facts, for callers that want nodes/edges instead
of flat lists (e.g. a future graph-visualisation UI)."""
from __future__ import annotations

from .case import Case
from .state import InvestigationState


def build_graph(case: Case, st: InvestigationState) -> dict:
    nodes = []
    for eid in st.discovered:
        e = case.evidence[eid]
        nodes.append({"id": eid, "kind": "evidence", "label": e.title, "type": e.type,
                      "opened": eid in st.viewed})
    for h in st.hypotheses.values():
        nodes.append({"id": h["id"], "kind": "hypothesis", "label": h["statement"],
                      "status": h["status"], "confidence": h["confidence"]})

    edges = []
    for l in st.links:
        edges.append({"source": l["evidence_id"], "target": l["hypothesis_id"],
                      "relation": l["relation"]})
    for f in st.flags:
        a, b = sorted(f["pair"])
        edges.append({"source": a, "target": b, "relation": "flagged_contradiction"})

    return {"nodes": nodes, "edges": edges}
