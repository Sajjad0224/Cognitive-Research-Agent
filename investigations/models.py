from django.db import models
from django.conf import settings


class CaseRecord(models.Model):
    case_id = models.CharField(max_length=100, unique=True)
    title = models.CharField(max_length=240)
    domain = models.CharField(max_length=120)
    difficulty = models.CharField(max_length=30)
    overview = models.TextField()
    objective = models.TextField()
    time_limit_minutes = models.PositiveIntegerField(null=True, blank=True)
    dataset = models.JSONField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("title",)

    def __str__(self):
        return f"{self.case_id}: {self.title}"


class Investigation(models.Model):
    session_id = models.CharField(max_length=40, primary_key=True)
    case = models.ForeignKey(CaseRecord, on_delete=models.PROTECT, related_name="investigations")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="investigations",
        null=True,
        blank=True,
    )
    browser_key = models.CharField(max_length=40, db_index=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at",)


class InvestigationEvent(models.Model):
    investigation = models.ForeignKey(Investigation, on_delete=models.CASCADE, related_name="events")
    event_id = models.CharField(max_length=40, unique=True)
    event_type = models.CharField(max_length=40)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ("id",)
        indexes = [models.Index(fields=("investigation", "id"))]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("investigation events are append-only")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("investigation events are append-only")


class ChatTurn(models.Model):
    investigation = models.ForeignKey(Investigation, on_delete=models.CASCADE, related_name="chat_turns")
    question = models.TextField()
    response = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
