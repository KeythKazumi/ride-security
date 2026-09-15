from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "is_read", "created_at", "read_at")
    list_filter = ("kind", "is_read")
    search_fields = ("title", "message")
    readonly_fields = ("created_at", "read_at")
