from django.db import models
from django.utils import timezone


class NotificationQuerySet(models.QuerySet):
    def unread(self):
        return self.filter(is_read=False)

    def mark_read(self):
        return self.filter(is_read=False).update(is_read=True, read_at=timezone.now())


class Notification(models.Model):
    """A message for whoever is watching the system.

    Deliberately not tied to a user: this is a single-operator local install, so
    a notification is read or unread for everyone.
    """

    class Kind(models.TextChoices):
        NEW_PERSON = "new_person", "New face detected"
        INFO = "info", "Information"
        WARNING = "warning", "Warning"

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.INFO)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    url = models.CharField(
        max_length=300,
        blank=True,
        help_text="Where clicking the notification should take you.",
    )
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["is_read", "-created_at"])]

    def __str__(self):
        return self.title

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])
        return self

    def mark_unread(self):
        if self.is_read:
            self.is_read = False
            self.read_at = None
            self.save(update_fields=["is_read", "read_at"])
        return self
