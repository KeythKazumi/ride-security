from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import Camera, NVR, Sensor
from .services import fetch_camera_snapshot


@login_required
def camera_list(request):
    return render(
        request,
        "devices/camera_list.html",
        {"cameras": Camera.objects.select_related("nvr")},
    )


@login_required
def camera_feed(request, camera_id):
    """Display a single camera with its live feed / snapshot."""
    camera = get_object_or_404(Camera, pk=camera_id)
    return render(
        request,
        "devices/camera_feed.html",
        {"camera": camera, "rtsp_url": camera.get_rtsp_url(), "snapshot_url": camera.get_snapshot_url()},
    )


@login_required
def camera_snapshot(request, camera_id):
    """Proxy a JPEG snapshot from the NVR / camera."""
    camera = get_object_or_404(Camera, pk=camera_id)
    image = fetch_camera_snapshot(camera)
    if image is None:
        return HttpResponse("Snapshot unavailable", status=502, content_type="text/plain")
    return HttpResponse(image, content_type="image/jpeg")


@login_required
def sensor_list(request):
    return render(request, "devices/sensor_list.html", {"sensors": Sensor.objects.all()})


@login_required
def nvr_list(request):
    return render(request, "devices/nvr_list.html", {"nvrs": NVR.objects.all()})
