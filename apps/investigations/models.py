import uuid
from django.conf import settings
from django.db import models
from apps.cases.models import Case,Evidence
class InvestigationSession(models.Model):
    class Status(models.TextChoices): ACTIVE='active','Active'; PAUSED='paused','Paused'; SUBMITTED='submitted','Submitted'; EVALUATED='evaluated','Evaluated'; ABANDONED='abandoned','Abandoned'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='investigation_sessions'); case=models.ForeignKey(Case,on_delete=models.PROTECT,related_name='sessions'); status=models.CharField(max_length=20,choices=Status.choices,default=Status.ACTIVE); started_at=models.DateTimeField(auto_now_add=True); last_activity_at=models.DateTimeField(auto_now=True); submitted_at=models.DateTimeField(null=True,blank=True); state=models.JSONField(default=dict,blank=True); final_answer=models.TextField(blank=True)
class EvidenceAccess(models.Model):
    session=models.ForeignKey(InvestigationSession,on_delete=models.CASCADE,related_name='evidence_accesses'); evidence=models.ForeignKey(Evidence,on_delete=models.PROTECT,related_name='session_accesses'); first_accessed_at=models.DateTimeField(auto_now_add=True); last_accessed_at=models.DateTimeField(auto_now=True); access_count=models.PositiveIntegerField(default=1); seconds_spent=models.PositiveIntegerField(default=0)
    class Meta: constraints=[models.UniqueConstraint(fields=['session','evidence'],name='uniq_session_evidence_access')]
class Hypothesis(models.Model):
    class Status(models.TextChoices): UNVERIFIED='unverified','Unverified'; SUPPORTED='supported','Supported'; REFUTED='refuted','Refuted'; ABANDONED='abandoned','Abandoned'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); session=models.ForeignKey(InvestigationSession,on_delete=models.CASCADE,related_name='hypotheses'); statement=models.TextField(); confidence=models.DecimalField(max_digits=5,decimal_places=4,default=.5); status=models.CharField(max_length=20,choices=Status.choices,default=Status.UNVERIFIED); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    evidence=models.ManyToManyField(Evidence,blank=True,related_name='user_hypotheses')
class InvestigationNote(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); session=models.ForeignKey(InvestigationSession,on_delete=models.CASCADE,related_name='notes'); title=models.CharField(max_length=200,blank=True); content=models.TextField(); tags=models.JSONField(default=list,blank=True); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
class InvestigationEvent(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); session=models.ForeignKey(InvestigationSession,on_delete=models.CASCADE,related_name='events'); event_type=models.CharField(max_length=60); occurred_at=models.DateTimeField(auto_now_add=True); payload=models.JSONField(default=dict,blank=True); sequence_number=models.PositiveIntegerField()
    class Meta: ordering=['sequence_number']; constraints=[models.UniqueConstraint(fields=['session','sequence_number'],name='uniq_event_sequence')]
