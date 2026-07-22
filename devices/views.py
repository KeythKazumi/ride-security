from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import Camera, NVR, Sensor


@login_required
def camera_list(request):
    return render(
        request,
        "devices/camera_list.html",
        {"cameras": Camera.objects.select_related("nvr")},
    )


@login_required
def sensor_list(request):
    return render(request, "devices/sensor_list.html", {"sensors": Sensor.objects.all()})


@login_required
def nvr_list(request):
    return render(request, "devices/nvr_list.html", {"nvrs": NVR.objects.all()})
