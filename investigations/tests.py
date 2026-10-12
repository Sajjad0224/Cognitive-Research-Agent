from pathlib import Path

from django.core.management import call_command
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from investigations.models import CaseRecord, ChatTurn, Investigation


class InvestigationJourneyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_cases", directory=Path(__file__).resolve().parents[1] / "cases", verbosity=0)

    def test_public_catalog_to_evidence_grounded_report(self):
        response = self.client.get(reverse("case_catalog"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Missing Research Data")
        self.assertContains(response, "Open case file")

        response = self.client.post(reverse("start_investigation", args=["CASE-014"]))
        self.assertEqual(response.status_code, 302)
        workspace_url = response["Location"]
        session_id = workspace_url.rstrip("/").split("/")[-1]
        response = self.client.get(workspace_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Ken Chen deliberately deleted")

        response = self.client.post(
            reverse("ask_case_agent", args=[session_id]),
            data={"text": "Who deleted the dataset? Show me the audit log."},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        answer = response.json()
        self.assertEqual([item["id"] for item in answer["new_evidence"]], ["E-04"])
        self.assertIn("[E-04] Server audit log", answer["response"])
        self.assertNotIn("issued by account kchen", answer["response"])
        self.assertNotIn("content", answer["new_evidence"][0])
        self.assertNotIn("14 outliers", answer["response"])
        self.assertEqual(ChatTurn.objects.filter(investigation_id=session_id).count(), 1)
        self.assertContains(self.client.get(workspace_url), "Who deleted the dataset?")

        response = self.client.post(reverse("open_evidence", args=[session_id, "E-04"]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("DELETE on /data/trial_X", response.json()["content"])
        response = self.client.post(
            reverse("ask_case_agent", args=[session_id]),
            data={"text": "Who deleted the dataset?"},
            content_type="application/json",
        )
        self.assertIn("issued by account kchen", response.json()["response"])

        response = self.client.post(
            reverse("ask_case_agent", args=[session_id]),
            data={"text": "When was the analysis notebook last edited?"},
            content_type="application/json",
        )
        self.assertNotIn("21:50", response.json()["response"])
        self.client.post(
            reverse("ask_case_agent", args=[session_id]),
            data={"text": "Find the backup snapshot."},
            content_type="application/json",
        )
        response = self.client.post(reverse("open_evidence", args=[session_id, "E-09"]))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("ask_case_agent", args=[session_id]),
            data={"text": "When was the analysis notebook last edited?"},
            content_type="application/json",
        )
        self.assertIn("21:50", response.json()["response"])

        response = self.client.post(
            reverse("submit_conclusion", args=[session_id]),
            data={
                "text": "Ken Chen deliberately deleted the data to conceal problems in the results.",
                "confidence": 0.9,
                "cited_evidence": ["E-04"],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["report_url"].endswith("/report/"))
        response = self.client.get(response.json()["report_url"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "How you investigated")
        self.assertContains(response, "Areas to improve")

        investigation = Investigation.objects.get(session_id=session_id)
        self.assertIsNotNone(investigation.completed_at)
        self.assertEqual(investigation.events.count(), 14)
        with self.assertRaises(ValueError):
            investigation.events.first().delete()

    def test_investigation_is_bound_to_the_anonymous_browser_session(self):
        response = self.client.post(reverse("start_investigation", args=["CASE-014"]))
        session_id = response["Location"].rstrip("/").split("/")[-1]
        other_browser = self.client_class()
        self.assertEqual(other_browser.get(reverse("investigation_workspace", args=[session_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("investigation_workspace", args=[session_id])).status_code, 200)

    def test_case_dataset_keeps_structured_case_sections(self):
        dataset = CaseRecord.objects.get(case_id="CASE-014").dataset
        self.assertTrue(dataset["suspects"])
        self.assertTrue(dataset["witness_statements"])
        self.assertTrue(dataset["timeline"])


class PlatformAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_cases", directory=Path(__file__).resolve().parents[1] / "cases", verbosity=0)

    def test_catalog_has_database_loaded_cases_and_public_statistics(self):
        response = self.client.get(reverse("case_catalog"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["case_total"], 10)
        self.assertContains(response, "The Locked Gallery")
        self.assertContains(response, "Midnight at Northstar")
        self.assertContains(response, "Total cases")
        self.assertContains(response, "Profile")

    def test_registration_saves_anonymous_progress_and_profile_is_user_specific(self):
        self.client.post(reverse("start_investigation", args=["CASE-101"]))
        response = self.client.post(reverse("register"), {
            "username": "investigator",
            "first_name": "Alex",
            "last_name": "Morgan",
            "email": "alex@example.test",
            "password1": "VerifiedExample2026!",
            "password2": "VerifiedExample2026!",
        })
        self.assertRedirects(response, reverse("profile"))
        user = User.objects.get(username="investigator")
        investigation = Investigation.objects.get(browser_key__isnull=False)
        self.assertEqual(investigation.user, user)
        self.assertEqual(self.client.get(reverse("profile")).context["attempted_total"], 1)
        self.assertEqual(self.client.get(reverse("case_catalog")).context["active_total"], 1)

        other_user = User.objects.create_user(username="another", password="VerifiedExample2026!")
        other_client = self.client_class()
        other_client.force_login(other_user)
        workspace_url = reverse("investigation_workspace", args=[investigation.session_id])
        self.assertEqual(other_client.get(workspace_url).status_code, 404)

    def test_login_and_logout_routes(self):
        User.objects.create_user(username="returning", password="VerifiedExample2026!")
        response = self.client.post(reverse("login"), {
            "username": "returning",
            "password": "VerifiedExample2026!",
        })
        self.assertRedirects(response, reverse("profile"))
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)
        self.assertRedirects(self.client.post(reverse("logout")), reverse("case_catalog"))
        self.assertRedirects(self.client.get(reverse("profile")), f"{reverse('login')}?next={reverse('profile')}")

    def test_solved_statistics_use_the_evaluated_case_outcome(self):
        user = User.objects.create_user(username="solver", password="VerifiedExample2026!")
        self.client.force_login(user)
        for conclusion in (
            "Ken Chen deliberately deleted the data to conceal problems in the results.",
            "Lina Park sabotaged the dataset because of a grudge against the PI.",
        ):
            response = self.client.post(reverse("start_investigation", args=["CASE-014"]))
            session_id = response["Location"].rstrip("/").split("/")[-1]
            self.client.post(
                reverse("submit_conclusion", args=[session_id]),
                data={"text": conclusion, "confidence": 0.8, "cited_evidence": []},
                content_type="application/json",
            )
        profile_response = self.client.get(reverse("profile"))
        self.assertEqual(profile_response.context["solved_total"], 1)
        self.assertEqual(profile_response.context["attempted_total"], 1)
