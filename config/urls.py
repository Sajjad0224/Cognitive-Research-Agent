from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include,path
from apps.cases.views import home_view,case_list_view,case_detail_view
from apps.investigations.views import workspace_view,report_view
urlpatterns=[
    path('admin/',admin.site.urls),
    path('accounts/login/',auth_views.LoginView.as_view(template_name='registration/login.html'),name='login'),
    path('accounts/logout/',auth_views.LogoutView.as_view(),name='logout'),
    path('privacy/',include('apps.profiles.urls')),
    path('',home_view,name='home'),
    path('cases/',case_list_view,name='case-list'),
    path('cases/<uuid:case_id>/',case_detail_view,name='case-detail'),
    path('investigations/<uuid:session_id>/',workspace_view,name='workspace'),
    path('investigations/<uuid:session_id>/report/',report_view,name='report'),
    path('api/cases/',include('apps.cases.urls')),
    path('api/investigations/',include('apps.investigations.urls'))
]
