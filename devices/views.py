from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .discovery import discover_devices, get_local_networks
from .forms import NVRForm
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


@login_required
def nvr_create(request):
    """Display and process the form to add a new NVR.

    Accepts query parameters matching form fields so the discovery page can
    prefill a candidate device.
    """
    if request.method == "POST":
        form = NVRForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("devices:nvrs")
    else:
        initial = {
            field: request.GET[field]
            for field in NVRForm.Meta.fields
            if request.GET.get(field)
        }
        form = NVRForm(initial=initial)
    return render(request, "devices/nvr_form.html", {"form": form})


@login_required
def nvr_discover(request):
    """Scan the local network for NVRs / cameras and list the candidates."""
    devices = []
    error = None
    scanned = False
    network = request.GET.get("network", "").strip()

    if request.GET.get("scan"):
        scanned = True
        try:
            devices = discover_devices(network=network or None)
        except (OSError, ValueError) as exc:
            error = str(exc)

    known_ips = set(NVR.objects.values_list("ip_address", flat=True))
    for device in devices:
        device["already_added"] = device["ip_address"] in known_ips

    return render(
        request,
        "devices/nvr_discover.html",
        {
            "devices": devices,
            "error": error,
            "scanned": scanned,
            "network": network,
            "local_networks": [str(net) for net in get_local_networks()],
        },
    )
