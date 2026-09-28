from django.db import transaction
from apps.cases.models import CaseHypothesis
from apps.investigations.services import investigation_features
from .models import EvaluationSnapshot, SkillProfile, SkillHistory
DEFAULT_WEIGHTS={'critical_thinking':.12,'analytical_reasoning':.12,'research_ability':.10,'logical_reasoning':.10,'evidence_evaluation':.12,'hypothesis_testing':.10,'decision_making':.08,'attention_to_detail':.08,'adaptability':.07,'communication':.04,'leadership':.03,'strategic_thinking':.04}
def clamp(x): return max(0,min(100,round(x,2)))
def evaluate_session(session):
    f=investigation_features(session); case=session.case; hypotheses=list(case.solution_hypotheses.all()); answer=session.final_answer.lower()
    required=[h for h in hypotheses if h.kind==CaseHypothesis.Kind.REQUIRED]; accepted=[h for h in hypotheses if h.kind in (CaseHypothesis.Kind.REQUIRED,CaseHypothesis.Kind.ACCEPTED)]
    matched=[]
    for h in accepted:
        terms=[t.lower() for t in h.statement.replace(',',' ').split() if len(t)>4][:8]
        if terms and sum(t in answer for t in terms)/len(terms)>=.35: matched.append(h)
    if required:
        conclusion_rate=len(matched)/max(1,len(required))*100
        evidence_requirements=[]
        accessed_ids=set(session.evidence_accesses.values_list('evidence_id',flat=True))
        for h in required:
            req=set(h.required_evidence.values_list('id',flat=True)); evidence_requirements.append(len(req & accessed_ids)/max(1,len(req)))
        evidence_support=(sum(evidence_requirements)/len(evidence_requirements))*100 if evidence_requirements else 0
        outcome=clamp(.70*conclusion_rate+.30*evidence_support)
    else:
        outcome=70 if answer else 0
    coverage=f['evidence_coverage']; testing=f['hypotheses_tested']/max(1,f['hypotheses_created'])*100; contradictions=clamp(f['contradictions_identified']*20); revisions=clamp(f['revisions']*25); support=clamp(f['evidence_backed_claims']*12); unsupported=clamp(f['unsupported_claims']*15); questions=clamp(f['questions']*6)
    evidence_eval=clamp(.45*coverage+.25*contradictions+.20*support+.10*(100-unsupported)); hypothesis=clamp(.55*testing+.25*revisions+.20*contradictions); research=clamp(.50*coverage+.30*questions+.20*(100-min(100,f['repeated_questions']*20)))
    critical=clamp(.40*evidence_eval+.25*hypothesis+.20*contradictions+.15*revisions); analytical=clamp(.40*coverage+.30*support+.20*hypothesis+.10*contradictions); logical=clamp(.45*support+.30*hypothesis+.25*evidence_eval); attention=clamp(.65*coverage+.35*contradictions); adaptability=clamp(.65*revisions+.35*contradictions); decision=clamp(.55*outcome+.25*evidence_eval+.20*adaptability); communication=clamp(50+f['questions']*4+support*.2-unsupported*.2); leadership=clamp(.45*decision+.30*research+.25*adaptability); strategy=clamp(.45*research+.30*analytical+.25*decision)
    skills={'critical_thinking':critical,'analytical_reasoning':analytical,'research_ability':research,'logical_reasoning':logical,'evidence_evaluation':evidence_eval,'hypothesis_testing':hypothesis,'decision_making':decision,'attention_to_detail':attention,'adaptability':adaptability,'communication':communication,'leadership':leadership,'strategic_thinking':strategy}
    weights=DEFAULT_WEIGHTS.copy(); weights.update({w.skill_key:float(w.weight) for w in case.skill_weights.all()}); total=sum(weights.values()) or 1; weights={k:v/total for k,v in weights.items()}; process=clamp(sum(skills[k]*weights.get(k,0) for k in skills)); reasoning=clamp(.4*evidence_eval+.25*hypothesis+.2*analytical+.15*logical); final=clamp(.45*outcome+.30*reasoning+.25*process)
    explanation={'strengths':[],'improvements':[],'reasoning':f'You accessed {f["evidence_accessed"]} evidence items, created {f["hypotheses_created"]} hypotheses, and tested {f["hypotheses_tested"]}.','decision_points':[],'missed_opportunities':[]}
    if coverage>=70: explanation['strengths'].append('Broad evidence coverage.')
    else: explanation['improvements'].append('Increase evidence coverage before committing to a conclusion.')
    if contradictions: explanation['strengths'].append('You explicitly investigated contradictions.')
    else: explanation['improvements'].append('Actively search for contradictory evidence.')
    if hypothesis>=70: explanation['strengths'].append('Hypotheses were tested against evidence.')
    else: explanation['improvements'].append('Test competing hypotheses earlier and more systematically.')
    if unsupported>20: explanation['improvements'].append('Reduce unsupported claims and distinguish assumptions from established facts.')
    snap,_=EvaluationSnapshot.objects.update_or_create(session=session,defaults={'outcome_score':outcome,'reasoning_score':reasoning,'process_score':process,'final_score':final,'skill_scores':skills,'feature_snapshot':f,'explanation':explanation})
    with transaction.atomic():
        for key,score in skills.items():
            profile,_=SkillProfile.objects.get_or_create(user=session.user,skill_key=key,defaults={'score':score,'sample_count':0}); old=float(profile.score); n=profile.sample_count; new=(old*n+score)/(n+1); profile.score=round(new,2); profile.sample_count=n+1; profile.confidence=min(1,(n+1)/10); profile.save(); SkillHistory.objects.create(profile=profile,session=session,previous_score=old,new_score=round(new,2),delta=round(new-old,2))
    session.status=session.Status.EVALUATED; session.save(update_fields=['status','last_activity_at']); return snap
