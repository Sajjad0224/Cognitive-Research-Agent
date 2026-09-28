from django.urls import path
from .views import privacy_view,export_data
urlpatterns=[path('',privacy_view,name='privacy'),path('export/',export_data,name='export-data')]
