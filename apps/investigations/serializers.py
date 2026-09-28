from rest_framework import serializers
from .models import InvestigationSession,InvestigationEvent
class InvestigationSessionSerializer(serializers.ModelSerializer):
    class Meta: model=InvestigationSession; fields=['id','case','status','started_at','last_activity_at','submitted_at','state']; read_only_fields=['id','status','started_at','last_activity_at','submitted_at','state']
class InvestigationEventSerializer(serializers.ModelSerializer):
    class Meta: model=InvestigationEvent; fields=['id','event_type','occurred_at','payload','sequence_number']; read_only_fields=['id','occurred_at','sequence_number']
