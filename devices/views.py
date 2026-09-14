import os
import tempfile

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .discovery import discover_devices, get_local_networks
from .events import analyze_event, annotate_event, record_motion_event
from .face_recognition import draw_recognized_faces, recognize_people
from .forms import CameraForm, NVRForm, SensorForm
from .models import Camera, MotionEvent, NVR, Sensor
from .services import fetch_camera_snapshot
from .streaming import generate_mjpeg_stream


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
def camera_stream(request, camera_id):
    """Return an MJPEG live stream from the camera's RTSP source."""
    camera = get_object_or_404(Camera, pk=camera_id)
    rtsp_url = camera.get_rtsp_url()
    if not rtsp_url:
        return HttpResponse("No RTSP URL configured for this camera", status=404, content_type="text/plain")
    return StreamingHttpResponse(
        generate_mjpeg_stream(camera, fps=20),
        content_type="multipart/x-mixed-replace; boundary=frame",
    )


@login_required
def camera_recognize(request, camera_id):
    """Return a JPEG snapshot with face recognition boxes and labels."""
    camera = get_object_or_404(Camera, pk=camera_id)
    image_bytes = fetch_camera_snapshot(camera)
    if not image_bytes:
        return HttpResponse("Snapshot unavailable", status=502, content_type="text/plain")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        faces = recognize_people(tmp_path)
        annotated = draw_recognized_faces(tmp_path, faces)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    if not annotated:
        return HttpResponse("Could not annotate image", status=502, content_type="text/plain")
    return HttpResponse(annotated, content_type="image/jpeg")


@login_required
def event_list(request):
    """Review captured motion events and whether each frame is recognition-grade."""
    events = MotionEvent.objects.select_related("camera")

    camera_id = request.GET.get("camera")
    if camera_id:
        events = events.filter(camera_id=camera_id)
    quality = request.GET.get("quality")
    if quality:
        events = events.filter(quality=quality)

    all_events = MotionEvent.objects.all()
    return render(
        request,
        "devices/event_list.html",
        {
            "events": events[:100],
            "cameras": Camera.objects.all(),
            "selected_camera": camera_id,
            "selected_quality": quality,
            "quality_choices": MotionEvent.Quality.choices,
            "stats": {
                "total": all_events.count(),
                "pending": all_events.filter(status=MotionEvent.Status.PENDING).count(),
                "usable": all_events.filter(quality=MotionEvent.Quality.USABLE).count(),
                "no_face": all_events.filter(quality=MotionEvent.Quality.NO_FACE).count(),
            },
        },
    )


@login_required
def event_detail(request, event_id):
    event = get_object_or_404(MotionEvent.objects.select_related("camera"), pk=event_id)
    return render(request, "devices/event_detail.html", {"event": event})


@login_required
def event_image(request, event_id):
    """Serve the stored frame through Django so it stays behind the login."""
    event = get_object_or_404(MotionEvent, pk=event_id)
    event.image.open("rb")
    try:
        return HttpResponse(event.image.read(), content_type="image/jpeg")
    finally:
        event.image.close()


@login_required
def event_annotated(request, event_id):
    event = get_object_or_404(MotionEvent, pk=event_id)
    annotated = annotate_event(event)
    if not annotated:
        return HttpResponse("No faces to annotate", status=404, content_type="text/plain")
    return HttpResponse(annotated, content_type="image/jpeg")


@login_required
def event_analyze(request, event_id):
    """Run analysis for a single event now rather than waiting for process_events."""
    event = get_object_or_404(MotionEvent.objects.select_related("camera"), pk=event_id)
    if request.method != "POST":
        return redirect("devices:event_detail", event_id=event.pk)

    analyze_event(event)
    if event.status == MotionEvent.Status.FAILED:
        messages.error(request, f"Analysis failed: {event.error}")
    else:
        messages.success(request, f"Analyzed: {event.get_quality_display()}.")
    return redirect("devices:event_detail", event_id=event.pk)


@login_required
def event_capture(request, camera_id):
    """Capture a frame on demand and analyze it immediately.

    Lets you judge whether a camera's framing and lighting can support face
    recognition without waiting for someone to walk past.
    """
    camera = get_object_or_404(Camera, pk=camera_id)
    if request.method != "POST":
        return redirect("devices:camera_feed", camera_id=camera.pk)

    event = record_motion_event(camera, MotionEvent.Source.MANUAL)
    if event is None:
        messages.error(request, f"Could not capture a frame from '{camera.name}'.")
        return redirect("devices:camera_feed", camera_id=camera.pk)

    analyze_event(event)
    if event.status == MotionEvent.Status.FAILED:
        messages.error(request, f"Captured, but analysis failed: {event.error}")
    else:
        messages.success(request, f"Captured and analyzed: {event.get_quality_display()}.")
    return redirect("devices:event_detail", event_id=event.pk)


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


@login_required
def nvr_edit(request, nvr_id):
    nvr = get_object_or_404(NVR, pk=nvr_id)
    if request.method == "POST":
        form = NVRForm(request.POST, instance=nvr)
        if form.is_valid():
            form.save()
            messages.success(request, f"NVR '{nvr.name}' updated.")
            return redirect("devices:nvrs")
    else:
        form = NVRForm(instance=nvr)
    return render(request, "devices/nvr_form.html", {"form": form, "is_edit": True, "nvr": nvr})


@login_required
def nvr_delete(request, nvr_id):
    nvr = get_object_or_404(NVR, pk=nvr_id)
    if request.method == "POST":
        nvr.delete()
        messages.success(request, f"NVR '{nvr.name}' removed.")
        return redirect("devices:nvrs")
    return render(request, "devices/confirm_delete.html", {"object": nvr, "type": "NVR", "list_url": "devices:nvrs"})


@login_required
def camera_create(request):
    if request.method == "POST":
        form = CameraForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Camera added.")
            return redirect("devices:cameras")
    else:
        form = CameraForm()
    return render(request, "devices/camera_form.html", {"form": form})


@login_required
def camera_edit(request, camera_id):
    camera = get_object_or_404(Camera, pk=camera_id)
    if request.method == "POST":
        form = CameraForm(request.POST, instance=camera)
        if form.is_valid():
            form.save()
            messages.success(request, f"Camera '{camera.name}' updated.")
            return redirect("devices:cameras")
    else:
        form = CameraForm(instance=camera)
    return render(request, "devices/camera_form.html", {"form": form, "is_edit": True, "camera": camera})


@login_required
def camera_delete(request, camera_id):
    camera = get_object_or_404(Camera, pk=camera_id)
    if request.method == "POST":
        camera.delete()
        messages.success(request, f"Camera '{camera.name}' removed.")
        return redirect("devices:cameras")
    return render(request, "devices/confirm_delete.html", {"object": camera, "type": "camera", "list_url": "devices:cameras"})


@login_required
def sensor_create(request):
    if request.method == "POST":
        form = SensorForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Sensor added.")
            return redirect("devices:sensors")
    else:
        form = SensorForm()
    return render(request, "devices/sensor_form.html", {"form": form})


@login_required
def sensor_edit(request, sensor_id):
    sensor = get_object_or_404(Sensor, pk=sensor_id)
    if request.method == "POST":
        form = SensorForm(request.POST, instance=sensor)
        if form.is_valid():
            form.save()
            messages.success(request, f"Sensor '{sensor.name}' updated.")
            return redirect("devices:sensors")
    else:
        form = SensorForm(instance=sensor)
    return render(request, "devices/sensor_form.html", {"form": form, "is_edit": True, "sensor": sensor})


@login_required
def sensor_delete(request, sensor_id):
    sensor = get_object_or_404(Sensor, pk=sensor_id)
    if request.method == "POST":
        sensor.delete()
        messages.success(request, f"Sensor '{sensor.name}' removed.")
        return redirect("devices:sensors")
    return render(request, "devices/confirm_delete.html", {"object": sensor, "type": "sensor", "list_url": "devices:sensors"})
