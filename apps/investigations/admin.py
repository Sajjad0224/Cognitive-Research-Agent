from django.contrib import admin
from .models import InvestigationSession,EvidenceAccess,Hypothesis,InvestigationNote,InvestigationEvent
@admin.register(InvestigationSession)
class InvestigationSessionAdmin(admin.ModelAdmin): list_display=('id','user','case','status','started_at','submitted_at'); list_filter=('status',); search_fields=('user__username','case__title')
admin.site.register(EvidenceAccess); admin.site.register(Hypothesis); admin.site.register(InvestigationNote); admin.site.register(InvestigationEvent)
