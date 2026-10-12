import json

from django import forms
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from cra.engine import InvestigationError
from investigations.models import CaseRecord, ChatTurn, Investigation
from investigations.services import (
    _write_lock,
    answer_from_database,
    engine_for_request,
    owned_investigation,
    report_for_session,
)


def _browser_key(request):
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def _json_body(request):
    try:
        body = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _solved_case_ids(investigations):
    completed = investigations.filter(completed_at__isnull=False).select_related("case")
    return {
        item.case_id
        for item in completed
        if report_for_session(item.session_id)["status"] == "solved"
    }


@require_GET
def case_catalog(request):
    cases = list(CaseRecord.objects.filter(is_active=True))
    recent = {}
    if request.session.session_key:
        progress = Investigation.objects.select_related("case")
        if request.user.is_authenticated:
            progress = progress.filter(
                Q(user=request.user)
                | Q(user__isnull=True, browser_key=request.session.session_key)
            )
        else:
            progress = progress.filter(
                user__isnull=True,
                browser_key=request.session.session_key,
            )
        for item in progress.order_by("-started_at"):
            recent.setdefault(item.case_id, item)
    for case in cases:
        case.recent_investigation = recent.get(case.pk)
    investigations = (
        Investigation.objects.filter(user=request.user)
        if request.user.is_authenticated else Investigation.objects.none()
    )
    return render(request, "investigations/case_catalog.html", {
        "cases": cases,
        "case_total": len(cases),
        "solved_total": len(_solved_case_ids(investigations)),
        "active_total": investigations.filter(completed_at__isnull=True).values("case_id").distinct().count(),
    })


@require_POST
def start_investigation(request, case_id):
    record = get_object_or_404(CaseRecord, case_id=case_id, is_active=True)
    owner = _browser_key(request)
    engine = engine_for_request()
    try:
        with _write_lock:
            session_id = engine.start_session(record.case_id, owner)
    except InvestigationError as exc:
        return render(request, "investigations/error.html", {"message": str(exc)}, status=400)
    if request.user.is_authenticated:
        Investigation.objects.filter(session_id=session_id).update(user=request.user)
    return redirect("investigation_workspace", session_id=session_id)


@require_GET
def investigation_workspace(request, session_id):
    try:
        investigation = owned_investigation(session_id, request)
    except Investigation.DoesNotExist:
        raise Http404("Investigation not found")
    engine = engine_for_request()
    state = engine.state(session_id)
    case = engine.cases[state.case_id]
    if state.closed:
        return redirect("investigation_report", session_id=session_id)
    return render(request, "investigations/workspace.html", {
        "investigation": investigation,
        "case": case.briefing(),
        "evidence": [case.evidence[eid].public() for eid in state.discovered],
        "chat_turns": investigation.chat_turns.all(),
        "chat_url": reverse("ask_case_agent", kwargs={"session_id": session_id}),
        "conclude_url": reverse("submit_conclusion", kwargs={"session_id": session_id}),
        "conclude_url": reverse("submit_conclusion", kwargs={"session_id": session_id}),
    })


@require_POST
def ask_case_agent(request, session_id):
    body = _json_body(request)
    if body is None or not isinstance(body.get("text"), str) or not body["text"].strip():
        return JsonResponse({"error": "Enter a non-empty question."}, status=400)
    if len(body["text"]) > 5000:
        return JsonResponse({"error": "Questions must be 5,000 characters or fewer."}, status=400)
    try:
        investigation = owned_investigation(session_id, request)
        engine = engine_for_request()
        with _write_lock, transaction.atomic():
            Investigation.objects.select_for_update().get(pk=investigation.pk)
            result = engine.ask(session_id, body["text"])
            state = engine.state(session_id)
            unlocked = result["new_evidence"] + result["adaptive_evidence"]
            result["response"] = answer_from_database(
                body["text"],
                [engine.cases[state.case_id].evidence[eid].public() for eid in state.viewed],
                investigation.case.dataset,
                unlocked,
            )
            result["new_evidence"] = [
                {key: item[key] for key in ("id", "title", "type")}
                for item in result["new_evidence"]
            ]
            result["adaptive_evidence"] = [
                {key: item[key] for key in ("id", "title", "type")}
                for item in result["adaptive_evidence"]
            ]
            ChatTurn.objects.create(
                investigation_id=session_id,
                question=body["text"],
                response=result["response"],
            )
        return JsonResponse(result)
    except Investigation.DoesNotExist:
        raise Http404("Investigation not found")
    except InvestigationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def open_evidence(request, session_id, evidence_id):
    try:
        investigation = owned_investigation(session_id, request)
        engine = engine_for_request()
        with _write_lock, transaction.atomic():
            Investigation.objects.select_for_update().get(pk=investigation.pk)
            evidence = engine.view_evidence(session_id, evidence_id)
        return JsonResponse(evidence)
    except Investigation.DoesNotExist:
        raise Http404("Investigation not found")
    except InvestigationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_POST
def submit_conclusion(request, session_id):
    body = _json_body(request)
    if body is None:
        return JsonResponse({"error": "The submitted conclusion must be valid JSON."}, status=400)
    text = body.get("text")
    confidence = body.get("confidence")
    cited = body.get("cited_evidence", [])
    if not isinstance(text, str) or not text.strip() or len(text) > 5000:
        return JsonResponse({"error": "Provide a conclusion of up to 5,000 characters."}, status=400)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return JsonResponse({"error": "Confidence must be between 0 and 100."}, status=400)
    if not isinstance(cited, list) or not all(isinstance(item, str) for item in cited):
        return JsonResponse({"error": "Select evidence from this investigation."}, status=400)
    try:
        investigation = owned_investigation(session_id, request)
        engine = engine_for_request()
        with _write_lock, transaction.atomic():
            Investigation.objects.select_for_update().get(pk=investigation.pk)
            engine.submit_conclusion(session_id, text, confidence, cited)
        return JsonResponse({"report_url": reverse("investigation_report", kwargs={"session_id": session_id})})
    except Investigation.DoesNotExist:
        raise Http404("Investigation not found")
    except InvestigationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def investigation_report(request, session_id):
    try:
        investigation = owned_investigation(session_id, request)
    except Investigation.DoesNotExist:
        raise Http404("Investigation not found")
    if not investigation.completed_at:
        raise Http404("Submit a conclusion to see the performance report.")
    return render(request, "investigations/report.html", {
        "report": report_for_session(session_id),
    })


class RegistrationForm(UserCreationForm):
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Last name")
    email = forms.EmailField()

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class PlatformAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"



def _claim_anonymous_progress(user, browser_key):
    if browser_key:
        Investigation.objects.filter(
            user__isnull=True,
            browser_key=browser_key,
        ).update(user=user)


def register(request):
    if request.user.is_authenticated:
        return redirect("profile")
    browser_key = request.session.session_key
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        _claim_anonymous_progress(user, browser_key)
        login(request, user)
        return redirect("profile")
    return render(request, "investigations/register.html", {"form": form})


class PlatformLoginView(LoginView):
    template_name = "investigations/login.html"
    authentication_form = PlatformAuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        browser_key = self.request.session.session_key
        response = super().form_valid(form)
        _claim_anonymous_progress(form.get_user(), browser_key)
        return response


@require_POST
def logout_user(request):
    logout(request)
    return redirect("case_catalog")


@login_required
@require_GET
def profile(request):
    investigations = Investigation.objects.filter(user=request.user).select_related("case")
    completed = investigations.filter(completed_at__isnull=False).order_by("-completed_at")
    reports = []
    solved_case_ids = set()
    for item in completed:
        report = report_for_session(item.session_id)
        if report["status"] == "solved":
            solved_case_ids.add(item.case_id)
        if len(reports) < 10:
            reports.append({"investigation": item, "report": report})
    return render(request, "investigations/profile.html", {
        "attempted_total": investigations.values("case_id").distinct().count(),
        "solved_total": len(solved_case_ids),
        "reports_total": completed.count(),
        "reports": reports,
    })
