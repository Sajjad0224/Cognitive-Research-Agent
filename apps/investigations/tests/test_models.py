from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.cases.models import Case
from apps.investigations.models import InvestigationSession,InvestigationEvent
class EventTests(TestCase):
    def test_sequence_is_unique_per_session(self):
        u=get_user_model().objects.create_user(username='u',password='pass12345'); c=Case.objects.create(title='T',slug='t2',domain='x',overview='x',objective='x',created_by=u); s=InvestigationSession.objects.create(user=u,case=c); InvestigationEvent.objects.create(session=s,event_type='a',sequence_number=1); self.assertEqual(s.events.count(),1)
