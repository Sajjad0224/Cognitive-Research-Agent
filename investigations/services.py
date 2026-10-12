import re
import threading

from django.db.models import Q

from cra.case import parse_case
from cra.engine import InvestigationEngine
from cra.matcher import KeywordMatcher
from cra.report import build_report
from investigations.models import CaseRecord, Investigation
from investigations.store import DjangoEventStore

STOP_WORDS = {
    "about", "after", "again", "also", "been", "before", "being", "between", "could",
    "does", "from", "have", "into", "what", "when", "where", "which", "while", "with",
    "would", "your", "there", "their", "this", "that", "were", "them", "then", "than",
    "who", "how", "why", "was", "the", "and", "for", "are", "you", "can", "did",
}
_write_lock = threading.RLock()


def _terms(text):
    words = {
        word for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) > 2 and word not in STOP_WORDS
    }
    normalized = set()
    for word in words:
        if len(word) > 5 and word.endswith("ed"):
            word = word[:-2]
            if word.endswith("t"):
                word += "e"
        normalized.add(word)
    return normalized


def case_catalog():
    rows = CaseRecord.objects.filter(is_active=True)
    return {row.case_id: parse_case(row.dataset) for row in rows}


def engine_for_request():
    return InvestigationEngine(DjangoEventStore(), case_catalog())


def owned_investigation(session_id, request, *, lock=False):
    query = Investigation.objects.select_related("case")
    if lock:
        query = query.select_for_update()
    if request.user.is_authenticated:
        query = query.filter(
            Q(user=request.user)
            | Q(user__isnull=True, browser_key=request.session.session_key)
        )
    else:
        query = query.filter(
            user__isnull=True,
            browser_key=request.session.session_key,
        )
    return query.get(session_id=session_id)


def answer_from_database(question, reviewed_evidence, dataset, new_evidence=()):
    """Retrieve only opened case material and cite each response to its source record."""
    terms = _terms(question)
    discovered_ids = {evidence["id"] for evidence in reviewed_evidence}
    sources = list(reviewed_evidence)
    for suspect in dataset.get("suspects", []):
        evidence_id = suspect.get("evidence_id")
        if evidence_id in discovered_ids:
            sources.append({
                "id": evidence_id,
                "title": f"Suspect profile: {suspect.get('name', 'Unknown')}",
                "content": f"{suspect.get('name', 'Unknown')}: {suspect.get('role', 'Role not recorded.')}",
            })
    for statement in dataset.get("witness_statements", []):
        evidence_id = statement.get("evidence_id")
        if evidence_id in discovered_ids:
            sources.append({
                "id": evidence_id,
                "title": f"Witness statement: {statement.get('person', 'Unknown')}",
                "content": statement.get("statement", ""),
            })
    for event in dataset.get("timeline", []):
        evidence_id = event.get("evidence_id")
        if evidence_id in discovered_ids:
            sources.append({
                "id": evidence_id,
                "title": f"Timeline: {event.get('time', 'Time unknown')}",
                "content": event.get("event", ""),
            })

    ranked = []
    for evidence in sources:
        text = f"{evidence['title']} {evidence['content']}"
        searchable = _terms(text)
        overlap = len(terms & searchable)
        ranked.append((overlap, evidence))
    ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
    matches = [evidence for score, evidence in ranked if score > 0][:3]
    if not matches:
        if new_evidence:
            unlocked = ", ".join(f"[{item['id']}] {item['title']}" for item in new_evidence)
            return (
                f"That line of inquiry uncovered: {unlocked}. Open the record in your evidence "
                "file and I can help examine what it says."
            )
        return "I can't establish that from the records you've opened. Review available evidence or try another lead."
    answer = ["Here is what the available case records say:"]
    answer.extend(f"[{item['id']} — {item['title']}] {item['content']}" for item in matches)
    answer.append("These are source excerpts; weigh their reliability and check for corroboration before concluding.")
    if new_evidence:
        unlocked = ", ".join(f"[{item['id']}] {item['title']}" for item in new_evidence)
        answer.append(f"New records are available to review: {unlocked}.")
    return "\n\n".join(answer)


def report_for_session(session_id):
    investigation = Investigation.objects.select_related("case").get(session_id=session_id)
    case = parse_case(investigation.case.dataset)
    events = DjangoEventStore().read(session_id)
    return build_report(case, events, matcher=KeywordMatcher())
