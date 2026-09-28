import uuid
from django.db import models
from apps.investigations.models import InvestigationSession
class EvaluationSnapshot(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); session=models.OneToOneField(InvestigationSession,on_delete=models.CASCADE,related_name='evaluation'); outcome_score=models.DecimalField(max_digits=6,decimal_places=2,default=0); reasoning_score=models.DecimalField(max_digits=6,decimal_places=2,default=0); process_score=models.DecimalField(max_digits=6,decimal_places=2,default=0); final_score=models.DecimalField(max_digits=6,decimal_places=2,default=0); skill_scores=models.JSONField(default=dict); feature_snapshot=models.JSONField(default=dict); explanation=models.JSONField(default=dict); algorithm_version=models.CharField(max_length=30,default='v1'); created_at=models.DateTimeField(auto_now_add=True)
class SkillProfile(models.Model):
    user=models.ForeignKey('auth.User',on_delete=models.CASCADE,related_name='skill_profiles'); skill_key=models.SlugField(max_length=80); score=models.DecimalField(max_digits=6,decimal_places=2,default=50); sample_count=models.PositiveIntegerField(default=0); confidence=models.DecimalField(max_digits=5,decimal_places=4,default=0); updated_at=models.DateTimeField(auto_now=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['user','skill_key'],name='uniq_user_skill')]
class SkillHistory(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); profile=models.ForeignKey(SkillProfile,on_delete=models.CASCADE,related_name='history'); session=models.ForeignKey(InvestigationSession,on_delete=models.CASCADE,related_name='skill_history'); previous_score=models.DecimalField(max_digits=6,decimal_places=2); new_score=models.DecimalField(max_digits=6,decimal_places=2); delta=models.DecimalField(max_digits=7,decimal_places=2); created_at=models.DateTimeField(auto_now_add=True)
