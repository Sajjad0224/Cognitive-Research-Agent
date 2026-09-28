from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404,render
from .models import Case
from apps.investigations.models import InvestigationSession
@login_required
def home_view(request):
    sessions=InvestigationSession.objects.filter(user=request.user).select_related('case').order_by('-last_activity_at')[:5]
    return render(request,'home.html',{'sessions':sessions,'cases':Case.objects.filter(is_published=True)[:6]})
@login_required
def case_list_view(request): return render(request,'cases/list.html',{'cases':Case.objects.filter(is_published=True)})
@login_required
def case_detail_view(request,case_id):
    case=get_object_or_404(Case,id=case_id,is_published=True); session=InvestigationSession.objects.filter(user=request.user,case=case,status__in=['active','paused']).first(); return render(request,'cases/detail.html',{'case':case,'session':session})
