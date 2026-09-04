from django.urls import path

from .views import (
    camera_create,
    camera_delete,
    camera_edit,
    camera_feed,
    camera_list,
    camera_snapshot,
    camera_stream,
    nvr_create,
    nvr_delete,
    nvr_discover,
    nvr_edit,
    nvr_list,
    sensor_create,
    sensor_delete,
    sensor_edit,
    sensor_list,
)

app_name = "devices"

urlpatterns = [
    path("cameras/", camera_list, name="cameras"),
    path("cameras/add/", camera_create, name="camera_add"),
    path("cameras/<int:camera_id>/edit/", camera_edit, name="camera_edit"),
    path("cameras/<int:camera_id>/delete/", camera_delete, name="camera_delete"),
    path("cameras/<int:camera_id>/feed/", camera_feed, name="camera_feed"),
    path("cameras/<int:camera_id>/snapshot/", camera_snapshot, name="camera_snapshot"),
    path("cameras/<int:camera_id>/stream/", camera_stream, name="camera_stream"),
    path("sensors/", sensor_list, name="sensors"),
    path("sensors/add/", sensor_create, name="sensor_add"),
    path("sensors/<int:sensor_id>/edit/", sensor_edit, name="sensor_edit"),
    path("sensors/<int:sensor_id>/delete/", sensor_delete, name="sensor_delete"),
    path("nvrs/", nvr_list, name="nvrs"),
    path("nvrs/add/", nvr_create, name="nvr_add"),
    path("nvrs/<int:nvr_id>/edit/", nvr_edit, name="nvr_edit"),
    path("nvrs/<int:nvr_id>/delete/", nvr_delete, name="nvr_delete"),
    path("nvrs/discover/", nvr_discover, name="nvr_discover"),
]
