from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.cases.models import Case,Evidence
class CaseTests(TestCase):
    def test_case_and_evidence(self):
        u=get_user_model().objects.create_user(username='u',password='pass12345'); c=Case.objects.create(title='T',slug='unique-test',domain='x',overview='o',objective='obj',created_by=u); Evidence.objects.create(case=c,title='E',evidence_type='document',content='c'); self.assertEqual(c.evidence.count(),1)
