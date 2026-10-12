from django.urls import path

from investigations import views

urlpatterns = [
    path("", views.case_catalog, name="case_catalog"),
    path("accounts/login/", views.PlatformLoginView.as_view(), name="login"),
    path("accounts/register/", views.register, name="register"),
    path("accounts/logout/", views.logout_user, name="logout"),
    path("profile/", views.profile, name="profile"),
    path("cases/<str:case_id>/start/", views.start_investigation, name="start_investigation"),
    path("investigations/<str:session_id>/", views.investigation_workspace, name="investigation_workspace"),
    path("investigations/<str:session_id>/ask/", views.ask_case_agent, name="ask_case_agent"),
    path("investigations/<str:session_id>/evidence/<str:evidence_id>/", views.open_evidence, name="open_evidence"),
    path("investigations/<str:session_id>/conclude/", views.submit_conclusion, name="submit_conclusion"),
    path("investigations/<str:session_id>/report/", views.investigation_report, name="investigation_report"),
]
