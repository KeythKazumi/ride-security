import os
import tempfile

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .discovery import discover_devices, get_local_networks
from .events import (
    analyze_event,
    annotate_event,
    cleanup_candidates,
    cleanup_reviewed_events,
    delete_event,
    record_motion_event,
    sighting_face_crop,
)
from .face_recognition import (
    PERSON_DB,
    db_path,
    draw_recognized_faces,
    invalidate_db_cache,
    search_faces,
)
from .forms import CameraForm, NVRForm, PersonForm, PersonNameForm, SensorForm
from .identities import (
    add_person_reference,
    approve_sighting,
    delete_candidate,
    delete_person,
    delete_person_reference,
    label_faces,
    merge_persons,
    name_person,
    person_references,
    promote_candidate,
    reject_sighting,
)
from .models import Camera, FaceCandidate, MotionEvent, NVR, Person, Sensor, Sighting
from .services import fetch_camera_snapshot
from .streaming import generate_mjpeg_stream


@login_required
def camera_list(request):
    # Ordered by NVR so the template can regroup them into one panel per NVR;
    # cameras with no NVR go last.
    return render(
        request,
        "devices/camera_list.html",
        {
            "cameras": Camera.objects.select_related("nvr").order_by(
                F("nvr__name").asc(nulls_last=True), "name"
            )
        },
    )


@login_required
def camera_feed(request, camera_id):
    """Display a single camera with its live feed / snapshot."""
    camera = get_object_or_404(Camera, pk=camera_id)
    # The RTSP/snapshot URLs embed the NVR credentials; never hand them to a template.
    return render(
        request,
        "devices/camera_feed.html",
        {"camera": camera, "has_rtsp": bool(camera.get_rtsp_url())},
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
    # A dashboard grid of several cameras asks for a low frame rate; the single
    # camera page uses the default.
    try:
        fps = min(max(int(request.GET.get("fps", 20)), 1), 30)
    except ValueError:
        fps = 20
    return StreamingHttpResponse(
        generate_mjpeg_stream(camera, fps=fps),
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

        faces = label_faces(search_faces(tmp_path))
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
                "to_cleanup": cleanup_candidates().count(),
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
def event_review(request, event_id):
    """Toggle the user's confirmation of an event's verdict.

    Confirmed no-face events become eligible for the scheduled cleanup.
    """
    event = get_object_or_404(MotionEvent, pk=event_id)
    if request.method != "POST":
        return redirect("devices:event_detail", event_id=event.pk)

    if event.is_reviewed:
        event.reviewed_at = None
        messages.info(request, "Confirmation withdrawn — this capture is kept.")
    else:
        event.reviewed_at = timezone.now()
        if event.quality == MotionEvent.Quality.NO_FACE:
            messages.success(request, "Verdict confirmed — this capture will be removed on the next cleanup pass.")
        else:
            messages.success(request, "Verdict confirmed.")
    event.save(update_fields=["reviewed_at"])
    return redirect("devices:event_detail", event_id=event.pk)


@login_required
def event_review_no_face(request):
    """Bulk-confirm every analyzed 'no face' verdict not yet reviewed."""
    if request.method != "POST":
        return redirect("devices:events")

    updated = MotionEvent.objects.filter(
        status=MotionEvent.Status.ANALYZED,
        quality=MotionEvent.Quality.NO_FACE,
        reviewed_at__isnull=True,
    ).update(reviewed_at=timezone.now())
    messages.success(
        request,
        f"Confirmed {updated} 'no face' verdict(s) — they go away on the next cleanup pass.",
    )
    return redirect("devices:events")


@login_required
def event_cleanup_now(request):
    """Run the same purge the scheduled task runs, immediately."""
    if request.method != "POST":
        return redirect("devices:events")

    removed, freed = cleanup_reviewed_events()
    if removed:
        messages.success(request, f"Removed {removed} confirmed capture(s), freed {freed / 1024:.0f} KB.")
    else:
        messages.info(request, "Nothing confirmed for cleanup.")
    return redirect("devices:events")


@login_required
def event_bulk_delete(request):
    """Delete every capture ticked on the list page in one POST."""
    if request.method != "POST":
        return redirect("devices:events")

    ids = request.POST.getlist("event_ids")
    if not ids:
        messages.info(request, "No captures selected.")
        return redirect("devices:events")

    removed, freed = 0, 0
    for event in MotionEvent.objects.filter(pk__in=ids):
        freed += delete_event(event)
        removed += 1
    messages.success(request, f"Removed {removed} capture(s), freed {freed / 1024:.0f} KB.")

    url = reverse("devices:events")
    query = request.GET.urlencode()
    return redirect(f"{url}?{query}" if query else url)


@login_required
def event_delete(request, event_id):
    """Remove a single capture and its stored frame, regardless of verdict."""
    event = get_object_or_404(MotionEvent, pk=event_id)
    if request.method == "POST":
        delete_event(event)
        messages.success(request, f"Capture {event_id} removed.")
        return redirect("devices:events")
    return render(
        request,
        "devices/confirm_delete.html",
        {"object": event, "type": "capture", "list_url": "devices:events"},
    )


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
def person_list(request):
    """The face database. Unnamed people first — those are the ones needing action."""
    people = Person.objects.annotate(sighting_total=Count("sightings")).order_by(
        "name", "-last_seen_at"
    )
    unnamed = [person for person in people if not person.is_named]
    named = [person for person in people if person.is_named]

    # Unnamed cards show the face where it was actually seen — the stored
    # reference may be a stale crop from an event that has been deleted.
    for person in unnamed:
        person.card_sighting_id = (
            person.sightings.order_by("-event__detected_at")
            .values_list("id", flat=True)
            .first()
        )

    return render(
        request,
        "devices/person_list.html",
        {
            "unnamed": unnamed,
            "named": named,
            "candidates": FaceCandidate.objects.select_related("camera"),
        },
    )


@login_required
def person_detail(request, person_id):
    person = get_object_or_404(Person, pk=person_id)
    primary = os.path.basename(person.reference_image.name or "")
    return render(
        request,
        "devices/person_detail.html",
        {
            "person": person,
            "references": [
                {"filename": name, "is_primary": name == primary}
                for name in person_references(person)
            ],
            "sightings": person.sightings.select_related("event", "event__camera")[:60],
            "sighting_total": person.sightings.count(),
        },
    )


@login_required
def person_add_reference(request, person_id):
    """Upload an extra reference photo — more angles mean better matching."""
    person = get_object_or_404(Person, pk=person_id)
    if request.method != "POST":
        return redirect("devices:person_detail", person_id=person.pk)

    image_file = request.FILES.get("reference")
    if not image_file:
        messages.error(request, "Choose an image file first.")
        return redirect("devices:person_detail", person_id=person.pk)

    add_person_reference(person, image_file)
    messages.success(request, "Reference photo added.")
    return redirect("devices:person_detail", person_id=person.pk)


@login_required
def person_reference_image(request, person_id, filename):
    """Serve one reference file from the person's directory."""
    person = get_object_or_404(Person, pk=person_id)
    if filename not in person_references(person):
        return HttpResponse("No such reference", status=404, content_type="text/plain")
    with open(db_path(PERSON_DB) / person.code / filename, "rb") as f:
        return HttpResponse(f.read(), content_type="image/jpeg")


@login_required
def person_delete_reference(request, person_id, filename):
    person = get_object_or_404(Person, pk=person_id)
    if request.method != "POST":
        return redirect("devices:person_detail", person_id=person.pk)

    try:
        delete_person_reference(person, filename)
        messages.success(request, "Reference photo removed.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("devices:person_detail", person_id=person.pk)


@login_required
def person_image(request, person_id):
    person = get_object_or_404(Person, pk=person_id)
    if not person.reference_image:
        return HttpResponse("No reference image", status=404, content_type="text/plain")
    try:
        person.reference_image.open("rb")
        try:
            return HttpResponse(person.reference_image.read(), content_type="image/jpeg")
        finally:
            person.reference_image.close()
    except OSError:
        return HttpResponse("Reference file missing", status=404, content_type="text/plain")


@login_required
def candidate_image(request, candidate_id):
    candidate = get_object_or_404(FaceCandidate, pk=candidate_id)
    candidate.image.open("rb")
    try:
        return HttpResponse(candidate.image.read(), content_type="image/jpeg")
    finally:
        candidate.image.close()


@login_required
def candidate_delete(request, candidate_id):
    """Discard a face candidate — e.g. a false positive or someone unwanted."""
    candidate = get_object_or_404(FaceCandidate, pk=candidate_id)
    if request.method == "POST":
        delete_candidate(candidate)
        messages.success(request, "Candidate removed.")
        return redirect("devices:people")
    return render(
        request,
        "devices/confirm_delete.html",
        {"object": candidate, "type": "candidate", "list_url": "devices:people"},
    )


@login_required
def person_bulk_delete(request):
    """Delete every unnamed person ticked on the People page in one POST.

    Restricted to unnamed people: named ones were a deliberate decision and
    get the per-person confirmation page instead.
    """
    if request.method != "POST":
        return redirect("devices:people")

    ids = request.POST.getlist("person_ids")
    people = list(Person.objects.filter(pk__in=ids, name=""))
    for person in people:
        delete_person(person)
    if people:
        messages.success(request, f"Removed {len(people)} unnamed person(s).")
    else:
        messages.info(request, "Nothing selected.")
    return redirect("devices:people")


@login_required
def candidate_bulk_delete(request):
    """Delete every candidate ticked on the People page in one POST."""
    if request.method != "POST":
        return redirect("devices:people")

    ids = request.POST.getlist("candidate_ids")
    candidates = list(FaceCandidate.objects.filter(pk__in=ids))
    for candidate in candidates:
        delete_candidate(candidate)
    if candidates:
        messages.success(request, f"Removed {len(candidates)} candidate(s).")
    else:
        messages.info(request, "Nothing selected.")
    return redirect("devices:people")


@login_required
def candidate_promote(request, candidate_id):
    """Manually promote a candidate to a person, skipping the sighting threshold."""
    candidate = get_object_or_404(FaceCandidate, pk=candidate_id)
    if request.method != "POST":
        return redirect("devices:people")

    person = promote_candidate(candidate)
    messages.success(request, "Candidate promoted — give this person a name.")
    return redirect("devices:person_name", person_id=person.pk)


@login_required
def person_name(request, person_id):
    """Name an auto-created person, or merge them into an existing one."""
    person = get_object_or_404(Person, pk=person_id)

    if request.method == "POST":
        form = PersonNameForm(request.POST, instance=person)
        if form.is_valid():
            merge_into = form.cleaned_data.get("merge_into")
            if merge_into:
                merge_persons(person, merge_into)
                messages.success(request, f"Merged into '{merge_into.display_name}'.")
                return redirect("devices:person_detail", person_id=merge_into.pk)

            name_person(person, form.cleaned_data["name"])
            messages.success(request, f"Named '{person.display_name}'.")
            return redirect("devices:person_detail", person_id=person.pk)
    else:
        form = PersonNameForm(instance=person)

    return render(
        request,
        "devices/person_name.html",
        {
            "form": form,
            "person": person,
            "sightings": person.sightings.select_related("event", "event__camera")[:12],
        },
    )


@login_required
def person_update(request, person_id):
    """Save the inline edits on the person page: name and/or a reference photo."""
    person = get_object_or_404(Person, pk=person_id)
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if name and name != person.name:
            name_person(person, name)
            messages.success(request, f"Renamed to '{person.display_name}'.")
        reference = request.FILES.get("reference")
        if reference:
            add_person_reference(person, reference)
            messages.success(request, "Reference photo added.")
    return redirect("devices:person_detail", person_id=person.pk)


@login_required
def person_create(request):
    if request.method == "POST":
        form = PersonForm(request.POST, request.FILES)
        if form.is_valid():
            person = form.save()
            invalidate_db_cache()
            messages.success(request, f"Enrolled '{person.display_name}'.")
            return redirect("devices:people")
    else:
        form = PersonForm()
    return render(request, "devices/person_form.html", {"form": form})


@login_required
def person_delete(request, person_id):
    person = get_object_or_404(Person, pk=person_id)
    if request.method == "POST":
        label = person.display_name
        delete_person(person)
        messages.success(request, f"Removed '{label}'.")
        return redirect("devices:people")
    return render(
        request,
        "devices/confirm_delete.html",
        {"object": person, "type": "person", "list_url": "devices:people"},
    )


def _back(request, fallback="devices:people"):
    """Redirect to the page the action was taken from (local paths only)."""
    target = request.POST.get("next", "")
    if target.startswith("/devices/") or target.startswith("/notifications"):
        return redirect(target)
    return redirect(fallback)


@login_required
def sighting_approve(request, sighting_id):
    """User confirmed this match is the person it claims."""
    sighting = get_object_or_404(Sighting, pk=sighting_id)
    if request.method == "POST":
        approve_sighting(sighting)
    return _back(request)


@login_required
def sighting_reject(request, sighting_id):
    """Match was wrong — delete it and feed the face back to the candidates."""
    sighting = get_object_or_404(Sighting, pk=sighting_id)
    if request.method == "POST":
        label = sighting.person.display_name
        reject_sighting(sighting)
        messages.info(request, f"Rejected the match to '{label}' — the face went back to candidates if usable.")
    return _back(request)


@login_required
def sighting_face(request, sighting_id):
    """JPEG crop of just the matched face, for judging the match."""
    sighting = get_object_or_404(Sighting.objects.select_related("event"), pk=sighting_id)
    crop = sighting_face_crop(sighting)
    if not crop:
        return HttpResponse("No face box", status=404, content_type="text/plain")
    return HttpResponse(crop, content_type="image/jpeg")


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
def camera_toggle_image(request, camera_id):
    """Show or hide the camera's image in the UI.

    Display-only: the RTSP connection and motion watcher are untouched.
    """
    camera = get_object_or_404(Camera, pk=camera_id)
    if request.method != "POST":
        return redirect("devices:cameras")

    camera.show_image = not camera.show_image
    camera.save(update_fields=["show_image", "updated_at"])
    return _back(request, fallback="devices:cameras")


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
