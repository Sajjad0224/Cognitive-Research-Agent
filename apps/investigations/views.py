import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.http import require_POST
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from .models import InvestigationSession,InvestigationEvent,Hypothesis
from .serializers import InvestigationSessionSerializer,InvestigationEventSerializer
from .services import access_evidence,accessible_evidence,create_hypothesis,submit_session,EventLogger
from apps.cases.models import Case,Evidence
from apps.ai.services import answer
from apps.evaluation.engine import evaluate_session
@login_required
def workspace_view(request,session_id):
    session=get_object_or_404(InvestigationSession.objects.select_related('case'),id=session_id,user=request.user); return render(request,'investigation/workspace.html',{'session':session,'evidence':accessible_evidence(session),'hypotheses':session.hypotheses.all(),'notes':session.notes.all()})
@login_required
def report_view(request,session_id):
    session=get_object_or_404(InvestigationSession,id=session_id,user=request.user); return render(request,'report.html',{'session':session,'evaluation':getattr(session,'evaluation',None),'profiles':request.user.skill_profiles.order_by('skill_key')})
@login_required
@require_POST
def start_session(request,case_id):
    case=get_object_or_404(Case,id=case_id,is_published=True); s=InvestigationSession.objects.filter(user=request.user,case=case,status__in=['active','paused']).first()
    if not s: s=InvestigationSession.objects.create(user=request.user,case=case,state={'started_from':'case_detail'}); EventLogger.log(s,'session_started',{'case_id':str(case.id)})
    return redirect('workspace',session_id=s.id)
@login_required
@require_POST
def api_agent(request,session_id):
    s=get_object_or_404(InvestigationSession,id=session_id,user=request.user); data=json.loads(request.body or '{}'); msg=str(data.get('message','')).strip()
    if not msg:return JsonResponse({'error':'message is required'},status=400)
    return JsonResponse({'answer':answer(s,msg)})
@login_required
@require_POST
def api_access(request,session_id,evidence_id):
    s=get_object_or_404(InvestigationSession,id=session_id,user=request.user); e=get_object_or_404(Evidence,id=evidence_id,case=s.case)
    try: access_evidence(s,e)
    except PermissionError as ex:return JsonResponse({'error':str(ex)},status=403)
    return JsonResponse({'id':str(e.id),'title':e.title,'content':e.content})
@login_required
@require_POST
def api_hypothesis(request,session_id):
    s=get_object_or_404(InvestigationSession,id=session_id,user=request.user); data=json.loads(request.body or '{}'); statement=str(data.get('statement','')).strip()
    if not statement:return JsonResponse({'error':'statement is required'},status=400)
    h=create_hypothesis(s,statement,data.get('confidence',.5)); return JsonResponse({'id':str(h.id),'statement':h.statement,'confidence':float(h.confidence)})
@login_required
@require_POST
def api_link_evidence(request,session_id,hypothesis_id,evidence_id):
    s=get_object_or_404(InvestigationSession,id=session_id,user=request.user); h=get_object_or_404(Hypothesis,id=hypothesis_id,session=s); e=get_object_or_404(Evidence,id=evidence_id,case=s.case)
    if not s.evidence_accesses.filter(evidence=e).exists(): return JsonResponse({'error':'Access the evidence before linking it.'},status=403)
    h.evidence.add(e); EventLogger.log(s,'evidence_linked',{'hypothesis_id':str(h.id),'evidence_id':str(e.id),'evidence_title':e.title}); return JsonResponse({'ok':True,'hypothesis_id':str(h.id),'evidence_id':str(e.id)})
@login_required
@require_POST
def api_contradiction(request,session_id):
    s=get_object_or_404(InvestigationSession,id=session_id,user=request.user); data=json.loads(request.body or '{}'); EventLogger.log(s,'contradiction_identified',{'description':str(data.get('description','')).strip(),'evidence_ids':data.get('evidence_ids',[])}) ; return JsonResponse({'ok':True})
@login_required
@require_POST
def api_submit(request,session_id):
    s=get_object_or_404(InvestigationSession,id=session_id,user=request.user); data=json.loads(request.body or '{}')
    try: submit_session(s,str(data.get('final_answer',''))); evaluate_session(s)
    except ValueError as ex:return JsonResponse({'error':str(ex)},status=409)
    return JsonResponse({'redirect':f'/investigations/{s.id}/report/'})
class SessionListCreateView(generics.ListCreateAPIView):
    serializer_class=InvestigationSessionSerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self): return InvestigationSession.objects.filter(user=self.request.user).select_related('case')
    def perform_create(self,serializer): serializer.save(user=self.request.user)
class SessionEventListView(generics.ListCreateAPIView):
    serializer_class=InvestigationEventSerializer; permission_classes=[IsAuthenticated]
    def get_queryset(self): return InvestigationEvent.objects.filter(session__id=self.kwargs['session_id'],session__user=self.request.user)
    def perform_create(self,serializer):
        s=get_object_or_404(InvestigationSession,id=self.kwargs['session_id'],user=self.request.user)
        allowed={'workspace_action','note_created','search_performed'}; event_type=serializer.validated_data.get('event_type')
        if event_type not in allowed:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'event_type':'This event type must be generated by the platform action APIs.'})
        last=s.events.order_by('-sequence_number').first(); serializer.save(session=s,sequence_number=(last.sequence_number+1 if last else 1))
