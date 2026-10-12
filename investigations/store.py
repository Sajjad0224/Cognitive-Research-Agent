from datetime import datetime, timezone
import uuid

from django.db import transaction

from cra.events import Event, SESSION_STARTED
from investigations.models import CaseRecord, Investigation, InvestigationEvent


class DjangoEventStore:
    """ORM implementation of the investigation engine's append-only event-store contract."""

    def append(self, session_id, case_id, user_id, event_type, payload=None):
        payload = payload or {}
        now = datetime.now(timezone.utc)
        with transaction.atomic():
            if event_type == SESSION_STARTED:
                case_record = CaseRecord.objects.get(case_id=case_id, is_active=True)
                investigation = Investigation.objects.create(
                    session_id=session_id,
                    case=case_record,
                    browser_key=user_id,
                    started_at=now,
                )
            else:
                investigation = Investigation.objects.select_for_update().get(session_id=session_id)
            row = InvestigationEvent.objects.create(
                investigation=investigation,
                event_id="EVT-" + uuid.uuid4().hex[:12],
                event_type=event_type,
                payload=payload,
                created_at=now,
            )
            if event_type == "conclusion_submitted":
                investigation.completed_at = now
                investigation.save(update_fields=("completed_at",))
        return Event(row.pk, row.event_id, session_id, case_id, user_id, now.isoformat(), event_type, payload)

    def read(self, session_id):
        investigation = Investigation.objects.get(session_id=session_id)
        return [
            Event(
                row.pk, row.event_id, session_id, investigation.case.case_id,
                investigation.browser_key, row.created_at.isoformat(), row.event_type, row.payload,
            )
            for row in investigation.events.all()
        ]

    def sessions_for_user(self, user_id):
        return list(Investigation.objects.filter(browser_key=user_id).values_list("session_id", flat=True))

    def all_sessions(self):
        return [{
            "session_id": row.session_id,
            "case_id": row.case.case_id,
            "user_id": row.browser_key,
            "started_at": row.started_at.isoformat(),
        } for row in Investigation.objects.select_related("case").all()]

    def has_sessions_for_case(self, case_id):
        return Investigation.objects.filter(case__case_id=case_id).exists()

    def export_for_user(self, user_id):
        investigations = Investigation.objects.filter(browser_key=user_id).select_related("case")
        events = InvestigationEvent.objects.filter(investigation__in=investigations).select_related("investigation", "investigation__case")
        return [
            Event(
                row.pk, row.event_id, row.investigation_id, row.investigation.case.case_id,
                user_id, row.created_at.isoformat(), row.event_type, row.payload,
            )
            for row in events
        ]
