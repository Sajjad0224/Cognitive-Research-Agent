from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404,render
from apps.investigations.models import InvestigationSession
@login_required
def evaluation_report(request,session_id):
    s=get_object_or_404(InvestigationSession,id=session_id,user=request.user); return render(request,'report.html',{'session':s,'evaluation':getattr(s,'evaluation',None),'profiles':request.user.skill_profiles.order_by('skill_key')})
