from rest_framework import serializers
from .models import Case
class EvidenceSerializer(serializers.ModelSerializer):
    class Meta: model=__import__('apps.cases.models',fromlist=['Evidence']).Evidence; fields=['id','title','evidence_type','content','visibility','metadata']
class CaseSerializer(serializers.ModelSerializer):
    evidence_count=serializers.SerializerMethodField()
    def get_evidence_count(self,obj): return obj.evidence.count()
    class Meta: model=Case; fields=['id','title','slug','domain','difficulty','overview','objective','rules','time_limit_seconds','evidence_count']
