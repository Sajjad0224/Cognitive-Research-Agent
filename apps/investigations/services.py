from django.db import transaction
from django.utils import timezone
from apps.cases.models import Evidence,EvidenceUnlockRule
from .models import InvestigationSession,InvestigationEvent,EvidenceAccess,Hypothesis,InvestigationNote
class EventLogger:
    @staticmethod
    @transaction.atomic
    def log(session,event_type,payload=None):
        try:
            if not session.user.preferences.consent_behavior_tracking and event_type not in {'session_started','solution_submitted'}: return None
        except Exception: pass
        last=session.events.select_for_update().order_by('-sequence_number').first()
        event=InvestigationEvent.objects.create(session=session,event_type=event_type,payload=payload or {},sequence_number=(last.sequence_number+1 if last else 1))
        InvestigationSession.objects.filter(pk=session.pk).update(last_activity_at=timezone.now())
        return event
def accessible_evidence(session):
    case=session.case; visible=list(case.evidence.filter(visibility=Evidence.Visibility.INITIAL)); accessed=set(session.evidence_accesses.values_list('evidence_id',flat=True))
    for rule in case.unlock_rules.select_related('target_evidence'):
        target=rule.target_evidence
        if target in visible: continue
        cfg=rule.config or {}; unlocked=False
        if rule.rule_type=='evidence_access': unlocked=str(cfg.get('evidence_id')) in {str(x) for x in accessed}
        elif rule.rule_type=='keyword':
            text=' '.join((e.content+' '+e.title).lower() for e in case.evidence.filter(id__in=accessed)); unlocked=all(str(k).lower() in text for k in cfg.get('keywords',[]))
        elif rule.rule_type=='hypothesis': unlocked=session.hypotheses.filter(statement__icontains=cfg.get('contains','')).exists()
        elif rule.rule_type=='event_count': unlocked=session.events.count() >= int(cfg.get('minimum',999999))
        if unlocked: visible.append(target)
    return Evidence.objects.filter(id__in=[e.id for e in visible])
def access_evidence(session,evidence):
    if evidence.case_id!=session.case_id: raise ValueError('Evidence does not belong to case.')
    if not accessible_evidence(session).filter(pk=evidence.pk).exists(): raise PermissionError('Evidence is not currently discoverable.')
    obj,created=EvidenceAccess.objects.get_or_create(session=session,evidence=evidence,defaults={'access_count':1})
    if not created: obj.access_count+=1; obj.save(update_fields=['access_count','last_accessed_at'])
    EventLogger.log(session,'evidence_accessed',{'evidence_id':str(evidence.id),'title':evidence.title,'first_access':created}); return obj
def create_hypothesis(session,statement,confidence=.5):
    h=Hypothesis.objects.create(session=session,statement=statement,confidence=max(0,min(1,float(confidence)))); EventLogger.log(session,'hypothesis_created',{'hypothesis_id':str(h.id),'statement':statement,'confidence':float(h.confidence)}); return h
def submit_session(session,final_answer):
    if session.status not in [InvestigationSession.Status.ACTIVE,InvestigationSession.Status.PAUSED]: raise ValueError('Session cannot be submitted.')
    session.status=InvestigationSession.Status.SUBMITTED; session.submitted_at=timezone.now(); session.final_answer=final_answer.strip(); session.save(update_fields=['status','submitted_at','final_answer','last_activity_at']); EventLogger.log(session,'solution_submitted',{'answer':session.final_answer}); return session
def investigation_features(session):
    events=list(session.events.all()); accesses=list(session.evidence_accesses.select_related('evidence')); hypotheses=list(session.hypotheses.all()); types={}
    for e in events: types[e.event_type]=types.get(e.event_type,0)+1
    contradiction_events=sum(1 for e in events if e.event_type=='contradiction_identified'); revisions=sum(1 for e in events if e.event_type in ('hypothesis_revised','assumption_revised')); supported_claims=sum(1 for e in events if e.event_type=='evidence_linked'); unsupported=sum(1 for e in events if e.event_type=='unsupported_claim'); tested=sum(1 for h in hypotheses if h.evidence.exists())
    return {'events':len(events),'event_types':types,'evidence_accessed':len(accesses),'evidence_total':session.case.evidence.count(),'evidence_coverage':round(len(accesses)/max(1,session.case.evidence.exclude(visibility=Evidence.Visibility.HIDDEN).count())*100,2),'hypotheses_created':len(hypotheses),'hypotheses_tested':tested,'contradictions_identified':contradiction_events,'revisions':revisions,'evidence_backed_claims':supported_claims,'unsupported_claims':unsupported,'questions':types.get('question_asked',0),'repeated_questions':types.get('repeated_question',0)}
