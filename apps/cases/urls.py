from django.urls import path
from rest_framework import generics
from .models import Case
from .serializers import CaseSerializer
class CaseListAPI(generics.ListAPIView): serializer_class=CaseSerializer; queryset=Case.objects.filter(is_published=True)
class CaseDetailAPI(generics.RetrieveAPIView): serializer_class=CaseSerializer; queryset=Case.objects.filter(is_published=True)
urlpatterns=[path('',CaseListAPI.as_view()),path('<uuid:pk>/',CaseDetailAPI.as_view())]
