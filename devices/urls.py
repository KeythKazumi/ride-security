from django.urls import path

from .views import camera_list, nvr_list, sensor_list

app_name = "devices"

urlpatterns = [
    path("cameras/", camera_list, name="cameras"),
    path("sensors/", sensor_list, name="sensors"),
    path("nvrs/", nvr_list, name="nvrs"),
]
