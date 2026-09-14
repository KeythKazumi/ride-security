"""Capture ingestion and deferred analysis for motion events.

Every trigger source funnels through `record_motion_event`, so adding a new one
(ONVIF pull-point, NVR OpenAPI push) means writing a transport and nothing else.

Capture and analysis are split on purpose. Recording a frame is a file write;
running SFace over it costs hundreds of milliseconds and loads TensorFlow. Doing
both inline would stall the detector loop during a burst of motion, so the
detector only ever calls `record_motion_event` and `manage.py process_events`
picks the rows up afterwards.
"""

import logging
import os
import tempfile
from contextlib import contextmanager

from django.core.files.base import ContentFile
from django.utils import timezone

from .face_recognition import assess_capture, draw_recognized_faces, recognize_people
from .models import MotionEvent, Person
from .services import fetch_camera_snapshot

logger = logging.getLogger(__name__)


@contextmanager
def _as_temp_file(image_bytes):
    """DeepFace wants a filesystem path, so spill the bytes to one."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name
        yield tmp_path
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def _event_bytes(event):
    """Read the stored frame, reopening so repeated reads always start at 0."""
    event.image.open("rb")
    try:
        return event.image.read()
    finally:
        event.image.close()


def record_motion_event(camera, source, image_bytes=None, motion_score=None):
    """Persist a captured frame as a pending MotionEvent.

    If `image_bytes` is None the frame is pulled from the camera now, which is
    what the manual "capture" button and event-driven transports without an
    attached image do. Returns None when no image could be obtained.
    """
    if image_bytes is None:
        image_bytes = fetch_camera_snapshot(camera)
    if not image_bytes:
        logger.warning("No image available for camera %s; dropping event", camera.id)
        return None

    event = MotionEvent(camera=camera, source=source, motion_score=motion_score)
    filename = f"{timezone.now():%Y%m%d-%H%M%S-%f}.jpg"
    event.image.save(filename, ContentFile(image_bytes), save=True)
    return event


def analyze_event(event):
    """Run face detection, quality scoring and recognition on a stored event.

    Detection runs unconditionally so we get a quality verdict even with zero
    enrolled people; recognition only runs when there is something to match
    against and a face was actually found.
    """
    try:
        with _as_temp_file(_event_bytes(event)) as image_path:
            assessment = assess_capture(image_path)
            faces = assessment["faces"]

            if faces and Person.objects.exists():
                matches = recognize_people(image_path)
                if matches:
                    faces = matches
                event.recognized = sorted(
                    {m["name"] for m in matches if m.get("name") and m["name"] != "Unknown"}
                )
            else:
                event.recognized = []

        event.face_count = assessment["face_count"]
        best_box = assessment["best_box"]
        event.best_face_width = best_box[2] if best_box else 0
        event.best_face_height = best_box[3] if best_box else 0
        event.blur_score = assessment["best_sharpness"]
        event.quality = assessment["quality"]
        event.faces = [
            {
                "box": list(face["box"]),
                "name": face.get("name"),
                "confidence": face.get("confidence"),
                "distance": face.get("distance"),
                "sharpness": face.get("sharpness"),
            }
            for face in faces
        ]
        event.status = MotionEvent.Status.ANALYZED
        event.error = ""
    except Exception as exc:
        logger.exception("Analysis failed for event %s", event.pk)
        event.status = MotionEvent.Status.FAILED
        event.error = str(exc)

    event.analyzed_at = timezone.now()
    event.save()
    return event


def annotate_event(event):
    """Return the event image with face boxes drawn, or None."""
    if not event.faces:
        return None
    with _as_temp_file(_event_bytes(event)) as image_path:
        return draw_recognized_faces(image_path, event.faces)


def process_pending(limit=None):
    """Analyze pending events oldest-first. Returns the events processed."""
    queryset = MotionEvent.objects.filter(
        status=MotionEvent.Status.PENDING
    ).select_related("camera").order_by("detected_at")
    if limit:
        queryset = queryset[:limit]

    return [analyze_event(event) for event in queryset]
