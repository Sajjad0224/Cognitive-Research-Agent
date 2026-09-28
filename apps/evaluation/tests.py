from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.cases.models import Case,CaseHypothesis,Evidence
from apps.investigations.models import InvestigationSession
from apps.evaluation.engine import evaluate_session
class EvaluationTests(TestCase):
    def test_evaluation_is_reproducible_shape(self):
        u=get_user_model().objects.create_user(username='u',password='pass12345'); c=Case.objects.create(title='Test',slug='eval-test',domain='Test',overview='x',objective='y',created_by=u); Evidence.objects.create(case=c,title='E',evidence_type='document',content='x'); CaseHypothesis.objects.create(case=c,code='H1',statement='the dataset was copied',kind='required'); s=InvestigationSession.objects.create(user=u,case=c,final_answer='the dataset was copied'); snap=evaluate_session(s); self.assertTrue(0<=float(snap.final_score)<=100); self.assertEqual(s.status,'evaluated')
