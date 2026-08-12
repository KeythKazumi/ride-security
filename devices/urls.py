from django.urls import path

from .views import (
    camera_feed,
    camera_list,
    camera_snapshot,
    nvr_create,
    nvr_discover,
    nvr_list,
    sensor_list,
)

app_name = "devices"

urlpatterns = [
    path("cameras/", camera_list, name="cameras"),
    path("cameras/<int:camera_id>/feed/", camera_feed, name="camera_feed"),
    path("cameras/<int:camera_id>/snapshot/", camera_snapshot, name="camera_snapshot"),
    path("sensors/", sensor_list, name="sensors"),
    path("nvrs/", nvr_list, name="nvrs"),
    path("nvrs/add/", nvr_create, name="nvr_add"),
    path("nvrs/discover/", nvr_discover, name="nvr_discover"),
]
