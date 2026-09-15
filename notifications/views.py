from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .models import Notification


@login_required
def notification_list(request):
    """Unread first by default; `?show=all` includes ones already read."""
    show_all = request.GET.get("show") == "all"
    notifications = Notification.objects.all() if show_all else Notification.objects.unread()

    return render(
        request,
        "notifications/notification_list.html",
        {
            "notifications": notifications[:200],
            "show_all": show_all,
            "unread_total": Notification.objects.unread().count(),
            "total": Notification.objects.count(),
        },
    )


@login_required
def notification_open(request, notification_id):
    """Mark one notification read, then follow it to wherever it points."""
    notification = get_object_or_404(Notification, pk=notification_id)
    notification.mark_read()
    return redirect(notification.url or "notifications:list")


@login_required
def notification_mark_read(request, notification_id):
    notification = get_object_or_404(Notification, pk=notification_id)
    if request.method == "POST":
        notification.mark_read()
    return redirect(request.POST.get("next") or "notifications:list")


@login_required
def notification_mark_all_read(request):
    if request.method == "POST":
        Notification.objects.mark_read()
    return redirect(request.POST.get("next") or "notifications:list")
