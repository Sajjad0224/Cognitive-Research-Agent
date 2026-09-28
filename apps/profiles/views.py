import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,render
from apps.investigations.models import InvestigationSession
from .models import UserPreference
@login_required
def privacy_view(request):
    pref,_=UserPreference.objects.get_or_create(user=request.user)
    if request.method=='POST':
        pref.consent_behavior_tracking=bool(request.POST.get('behavior_tracking')); pref.consent_personalization=bool(request.POST.get('personalization')); pref.save(); return render(request,'privacy.html',{'preference':pref,'saved':True})
    return render(request,'privacy.html',{'preference':pref})
@login_required
def export_data(request):
    sessions=InvestigationSession.objects.filter(user=request.user).select_related('case').prefetch_related('events','hypotheses','evidence_accesses')
    data={'user':{'id':request.user.id,'username':request.user.username},'investigations':[]}
    for s in sessions:
        data['investigations'].append({'id':str(s.id),'case':s.case.title,'status':s.status,'events':[{'type':e.event_type,'at':e.occurred_at.isoformat(),'payload':e.payload} for e in s.events.all()]})
    return JsonResponse(data,json_dumps_params={'indent':2})
