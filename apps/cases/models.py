import uuid
from django.conf import settings
from django.db import models
class Case(models.Model):
    class Difficulty(models.TextChoices): BEGINNER='beginner','Beginner'; INTERMEDIATE='intermediate','Intermediate'; ADVANCED='advanced','Advanced'; EXPERT='expert','Expert'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); title=models.CharField(max_length=200); slug=models.SlugField(max_length=220,unique=True); domain=models.CharField(max_length=100); difficulty=models.CharField(max_length=20,choices=Difficulty.choices,default=Difficulty.INTERMEDIATE); overview=models.TextField(); objective=models.TextField(); rules=models.JSONField(default=list,blank=True); time_limit_seconds=models.PositiveIntegerField(null=True,blank=True); is_published=models.BooleanField(default=False); created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='authored_cases'); created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: ordering=['-created_at']
    def __str__(self): return self.title
class CaseEntity(models.Model):
    class EntityType(models.TextChoices): PERSON='person','Person'; ORGANIZATION='organization','Organization'; LOCATION='location','Location'; EVENT='event','Event'; OBJECT='object','Object'; OTHER='other','Other'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); case=models.ForeignKey(Case,on_delete=models.CASCADE,related_name='entities'); name=models.CharField(max_length=200); entity_type=models.CharField(max_length=30,choices=EntityType.choices); description=models.TextField(blank=True); is_hidden=models.BooleanField(default=False)
    class Meta: constraints=[models.UniqueConstraint(fields=['case','name'],name='uniq_case_entity_name')]
    def __str__(self): return self.name
class EntityRelationship(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); case=models.ForeignKey(Case,on_delete=models.CASCADE,related_name='relationships'); source=models.ForeignKey(CaseEntity,on_delete=models.CASCADE,related_name='outgoing_relationships'); target=models.ForeignKey(CaseEntity,on_delete=models.CASCADE,related_name='incoming_relationships'); relation=models.CharField(max_length=80); description=models.TextField(blank=True); is_hidden=models.BooleanField(default=False)
class Evidence(models.Model):
    class EvidenceType(models.TextChoices): DOCUMENT='document','Document'; REPORT='report','Report'; STATEMENT='statement','Statement'; IMAGE='image','Image'; MESSAGE='message','Message'; TIMELINE='timeline','Timeline'; FINANCIAL='financial_record','Financial Record'; LOG='log','Log'; CHART='chart','Chart'; OTHER='other','Other'
    class Visibility(models.TextChoices): INITIAL='initial','Initial'; DISCOVERABLE='discoverable','Discoverable'; CONDITIONAL='conditional','Conditional'; HIDDEN='hidden','Hidden'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); case=models.ForeignKey(Case,on_delete=models.CASCADE,related_name='evidence'); title=models.CharField(max_length=200); evidence_type=models.CharField(max_length=30,choices=EvidenceType.choices); content=models.TextField(); visibility=models.CharField(max_length=20,choices=Visibility.choices,default=Visibility.INITIAL); reliability=models.DecimalField(max_digits=5,decimal_places=4,default=.5); relevance=models.DecimalField(max_digits=5,decimal_places=4,default=.5); directness=models.DecimalField(max_digits=5,decimal_places=4,default=.5); is_red_herring=models.BooleanField(default=False); metadata=models.JSONField(default=dict,blank=True); created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['created_at']; indexes=[models.Index(fields=['case','visibility'])]
    def __str__(self): return self.title
class EvidenceRelation(models.Model):
    RELATIONS=[('supports','Supports'),('contradicts','Contradicts'),('corroborates','Corroborates'),('references','References')]
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); source=models.ForeignKey(Evidence,on_delete=models.CASCADE,related_name='outgoing_relations'); target=models.ForeignKey(Evidence,on_delete=models.CASCADE,related_name='incoming_relations'); relation=models.CharField(max_length=30,choices=RELATIONS); notes=models.TextField(blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['source','target','relation'],name='uniq_evidence_relation')]
class CaseHypothesis(models.Model):
    class Kind(models.TextChoices): REQUIRED='required','Required'; ACCEPTED='accepted','Accepted'; REJECTED='rejected','Rejected'; PARTIAL='partial','Partial'
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); case=models.ForeignKey(Case,on_delete=models.CASCADE,related_name='solution_hypotheses'); code=models.CharField(max_length=40); statement=models.TextField(); kind=models.CharField(max_length=20,choices=Kind.choices); minimum_evidence=models.PositiveIntegerField(default=1); required_evidence=models.ManyToManyField(Evidence,blank=True,related_name='required_for_hypotheses'); explanation=models.TextField(blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['case','code'],name='uniq_case_hypothesis_code')]
class EvidenceUnlockRule(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False); case=models.ForeignKey(Case,on_delete=models.CASCADE,related_name='unlock_rules'); target_evidence=models.OneToOneField(Evidence,on_delete=models.CASCADE,related_name='unlock_rule'); rule_type=models.CharField(max_length=40,choices=[('evidence_access','Evidence access'),('keyword','Keyword'),('hypothesis','Hypothesis'),('event_count','Event count')]); config=models.JSONField(default=dict); description=models.TextField(blank=True)
class CaseSkillWeight(models.Model):
    case=models.ForeignKey(Case,on_delete=models.CASCADE,related_name='skill_weights'); skill_key=models.SlugField(max_length=80); weight=models.DecimalField(max_digits=6,decimal_places=4)
    class Meta: constraints=[models.UniqueConstraint(fields=['case','skill_key'],name='uniq_case_skill_weight')]
class AdaptiveCasePolicy(models.Model):
    case=models.OneToOneField(Case,on_delete=models.CASCADE,related_name='adaptive_policy'); enabled=models.BooleanField(default=False); rules=models.JSONField(default=list,blank=True); preserve_truth=models.BooleanField(default=True)
