from django.urls import path

from .views import (
    notification_list,
    notification_mark_all_read,
    notification_mark_read,
    notification_open,
)

app_name = "notifications"

urlpatterns = [
    path("", notification_list, name="list"),
    path("<int:notification_id>/open/", notification_open, name="open"),
    path("<int:notification_id>/read/", notification_mark_read, name="read"),
    path("read-all/", notification_mark_all_read, name="read_all"),
]
