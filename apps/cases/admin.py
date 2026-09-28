from django.contrib import admin
from .models import Case,CaseEntity,EntityRelationship,Evidence,EvidenceRelation,CaseHypothesis,EvidenceUnlockRule,CaseSkillWeight,AdaptiveCasePolicy
@admin.register(Case)
class CaseAdmin(admin.ModelAdmin): list_display=('title','domain','difficulty','is_published','created_at'); list_filter=('difficulty','domain','is_published'); prepopulated_fields={'slug':('title',)}; search_fields=('title','overview','objective')
admin.site.register(CaseEntity); admin.site.register(EntityRelationship); admin.site.register(Evidence); admin.site.register(EvidenceRelation); admin.site.register(CaseHypothesis); admin.site.register(EvidenceUnlockRule); admin.site.register(CaseSkillWeight); admin.site.register(AdaptiveCasePolicy)
