from django.conf import settings
from django.db import models
class UserPreference(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='preferences'); consent_behavior_tracking=models.BooleanField(default=True); consent_personalization=models.BooleanField(default=True); updated_at=models.DateTimeField(auto_now=True)
