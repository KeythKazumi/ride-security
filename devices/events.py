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
import uuid
from contextlib import contextmanager

from django.core.files.base import ContentFile
from django.utils import timezone

from .face_recognition import assess_capture, crop_face, detect_persons, draw_recognized_faces
from .identities import identify_faces, label_faces, register_sightings
from .models import MotionEvent
from .services import fetch_camera_snapshot

logger = logging.getLogger(__name__)

# Frames kept per motion episode. The rest are discarded: they are the same
# person a step earlier or later, and storage is the scarce resource.
DEFAULT_KEEP_FRAMES = 5
# Weight of "how close is the person" versus "how sharp is the frame" when
# ranking an episode's frames. Distance dominates: a slightly soft face at
# 100px beats a razor-sharp one at 20px every time.
CLOSENESS_WEIGHT = 0.7


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


def record_motion_event(camera, source, image_bytes=None, motion_score=None, require_person=None):
    """Persist a captured frame as a pending MotionEvent.

    If `image_bytes` is None the frame is pulled from the camera now, which is
    what the manual "capture" button and event-driven transports without an
    attached image do. Returns None when no image could be obtained.

    Automatic sources only keep frames with a person in them: motion alone is
    a cat, a car or the sun moving, and those captures were most of the table.
    A manual capture is the operator asking for *this* frame, so it is always
    kept. The person boxes found here are stored so analysis can reuse them.
    """
    if image_bytes is None:
        image_bytes = fetch_camera_snapshot(camera)
    if not image_bytes:
        logger.warning("No image available for camera %s; dropping event", camera.id)
        return None

    if require_person is None:
        require_person = source != MotionEvent.Source.MANUAL

    persons = detect_persons(image_bytes)
    if require_person and not persons:
        logger.info("No person in %s capture from camera %s; dropping", source, camera.id)
        return None

    return _save_event(camera, source, image_bytes, persons, motion_score=motion_score)


def _save_event(camera, source, image_bytes, persons, motion_score=None, detected_at=None, sequence="", rank=0):
    event = MotionEvent(
        camera=camera,
        source=source,
        motion_score=motion_score,
        persons=persons,
        sequence=sequence,
        rank=rank,
    )
    if detected_at:
        event.detected_at = detected_at
    filename = f"{event.detected_at:%Y%m%d-%H%M%S-%f}.jpg"
    event.image.save(filename, ContentFile(image_bytes), save=True)
    return event


def _person_height_ratio(persons, frame_h):
    """Largest person box height as a fraction of the frame — a proxy for distance."""
    if not persons or not frame_h:
        return 0.0
    return max(box[3] for box in persons) / frame_h


def record_motion_episode(camera, episode, keep=DEFAULT_KEEP_FRAMES):
    """Store the most useful frames of a motion episode as one sequence.

    Every frame is run through the person detector; frames without a person
    are dropped outright. The rest are ranked by how large the person is (they
    are closer, so the face has more pixels) and how sharp the frame is, and
    the top `keep` are saved with `rank` 0 as the best. The processor analyses
    a sequence best-first, so recognition gets the good shot even when the
    first frame of the episode was a speck at the far end of the street.

    Returns the saved events, best first.
    """
    from .face_recognition import _load_image

    scored = []
    for frame in episode.frames:
        persons = detect_persons(frame.jpeg)
        if not persons:
            continue
        image = _load_image(frame.jpeg)
        frame_h = image.shape[0] if image is not None else 0
        scored.append((frame, persons, _person_height_ratio(persons, frame_h)))

    if not scored:
        logger.info(
            "Episode on camera %s: %s frame(s), no person in any; dropping",
            camera.id,
            len(episode.frames),
        )
        return []

    max_sharp = max(frame.sharpness for frame, _, _ in scored) or 1.0
    max_close = max(close for _, _, close in scored) or 1.0

    def rank_key(item):
        frame, _, close = item
        return CLOSENESS_WEIGHT * (close / max_close) + (1 - CLOSENESS_WEIGHT) * (frame.sharpness / max_sharp)

    scored.sort(key=rank_key, reverse=True)
    sequence = uuid.uuid4().hex

    events = [
        _save_event(
            camera,
            MotionEvent.Source.FRAME_DIFF,
            frame.jpeg,
            persons,
            motion_score=frame.motion_score,
            detected_at=frame.captured_at,
            sequence=sequence,
            rank=rank,
        )
        for rank, (frame, persons, _) in enumerate(scored[:keep] if keep else scored)
    ]
    logger.info(
        "Episode on camera %s: kept %s of %s frame(s) (%s with a person) as sequence %s",
        camera.id,
        len(events),
        len(episode.frames),
        len(scored),
        sequence[:8],
    )
    return events


def analyze_event(event):
    """Score a capture, then match every face against the identity database.

    Detection always runs so a quality verdict exists even with nothing
    enrolled. Identification then either links a known `Person` or feeds the
    face into the candidate pipeline, which may auto-create a person.
    """
    try:
        with _as_temp_file(_event_bytes(event)) as image_path:
            assessment = assess_capture(image_path)
            detected = assessment["faces"]

            event.face_count = assessment["face_count"]
            best_box = assessment["best_box"]
            event.best_face_width = best_box[2] if best_box else 0
            event.best_face_height = best_box[3] if best_box else 0
            event.blur_score = assessment["best_sharpness"]
            event.quality = assessment["quality"]
            event.persons = assessment["persons"]

            # `event` must be saved before sightings can reference it.
            event.status = MotionEvent.Status.ANALYZED
            event.error = ""
            event.analyzed_at = timezone.now()
            # A new verdict invalidates any earlier user confirmation.
            event.reviewed_at = None
            event.save()

            if detected:
                matches = identify_faces(image_path, [face["box"] for face in detected])
                register_sightings(event, matches, image_path)
                faces = label_faces(matches)
            else:
                event.sightings.all().delete()
                faces = []

        sharpness_by_box = {tuple(face["box"]): face.get("sharpness") for face in detected}
        event.faces = [
            {
                "box": list(face["box"]),
                "name": face.get("name"),
                "code": face.get("code"),
                "distance": face.get("distance"),
                "sharpness": sharpness_by_box.get(tuple(face["box"])),
            }
            for face in faces
        ]
        event.recognized = sorted(
            {
                sighting.person.display_name
                for sighting in event.sightings.select_related("person")
            }
        )
    except Exception as exc:
        logger.exception("Analysis failed for event %s", event.pk)
        event.status = MotionEvent.Status.FAILED
        event.error = str(exc)

    event.analyzed_at = timezone.now()
    event.save()
    return event


def annotate_event(event):
    """Return the event image with face/person boxes drawn, or None."""
    if not event.faces and not event.persons:
        return None
    with _as_temp_file(_event_bytes(event)) as image_path:
        return draw_recognized_faces(image_path, event.faces, persons=event.persons)


def sighting_face_crop(sighting):
    """JPEG crop of a sighting's face box — what the user judges a match by."""
    if not sighting.box:
        return None
    with _as_temp_file(_event_bytes(sighting.event)) as image_path:
        return crop_face(image_path, sighting.box, margin=0.5)


def process_pending(limit=None):
    """Analyze pending events oldest-first. Returns the events processed."""
    queryset = MotionEvent.objects.filter(
        status=MotionEvent.Status.PENDING
    ).select_related("camera").order_by("detected_at")
    if limit:
        queryset = queryset[:limit]

    # Oldest sequence first, best shot within it first. Sequence ids are
    # random, so "oldest" is decided by when a sequence first shows up in
    # chronological order; a stable sort then pulls rank 0 to the front.
    events = list(queryset)
    first_seen = {}
    for index, event in enumerate(events):
        first_seen.setdefault(event.sequence or event.pk, index)
    events.sort(key=lambda e: (first_seen[e.sequence or e.pk], e.rank))

    return [analyze_event(event) for event in events]


def delete_event(event):
    """Remove a capture and its stored frame. Returns bytes freed.

    Django does not remove files when a row is deleted, so the image is
    deleted explicitly first.
    """
    try:
        freed = event.image.size
    except (OSError, ValueError):
        freed = 0
    event.image.delete(save=False)
    event.delete()
    return freed


def cleanup_candidates(require_review=True):
    """Analyzed events with neither a face nor a person in them.

    By default only those a user has confirmed are returned, so the periodic
    cleaner never deletes on an unverified verdict. `require_review=False`
    returns every empty capture, for a one-off purge of accumulated noise.
    """
    queryset = MotionEvent.objects.filter(
        status=MotionEvent.Status.ANALYZED,
        quality=MotionEvent.Quality.NO_FACE,
        face_count=0,
    ).filter(persons__in=([], None))
    if require_review:
        queryset = queryset.filter(reviewed_at__isnull=False)
    return queryset


def cleanup_reviewed_events(queryset=None):
    """Delete confirmed no-face captures and their image files.

    Returns (events_removed, bytes_freed).
    """
    removed = 0
    freed = 0
    for event in (queryset if queryset is not None else cleanup_candidates()).iterator():
        freed += delete_event(event)
        removed += 1
    return removed, freed
