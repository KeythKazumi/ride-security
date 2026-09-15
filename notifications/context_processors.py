from .models import Notification

# Notifications shown inline in the sidebar dropdown before "view all" is needed.
DROPDOWN_LIMIT = 8


def notifications(request):
    """Expose unread notifications to every template for the sidebar badge."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}

    unread = Notification.objects.unread()
    return {
        "notification_unread_count": unread.count(),
        "notification_unread": unread[:DROPDOWN_LIMIT],
    }
