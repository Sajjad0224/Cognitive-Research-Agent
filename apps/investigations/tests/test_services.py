from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.cases.models import Case,Evidence
from apps.investigations.models import InvestigationSession
from apps.investigations.services import access_evidence,accessible_evidence,create_hypothesis
class ServiceTests(TestCase):
    def setUp(self):
        u=get_user_model().objects.create_user(username='u',password='pass12345'); self.case=Case.objects.create(title='T',slug='t',domain='x',overview='x',objective='x',created_by=u,is_published=True); self.initial=Evidence.objects.create(case=self.case,title='Initial',evidence_type='document',content='hello'); self.hidden=Evidence.objects.create(case=self.case,title='Hidden',evidence_type='document',content='secret',visibility='hidden'); self.s=InvestigationSession.objects.create(user=u,case=self.case)
    def test_initial_access(self): self.assertEqual(accessible_evidence(self.s).count(),1); access_evidence(self.s,self.initial); self.assertEqual(self.s.evidence_accesses.count(),1)
    def test_hypothesis_event(self): create_hypothesis(self.s,'A theory'); self.assertEqual(self.s.events.filter(event_type='hypothesis_created').count(),1)
