"""Building the face database: matching, auto-enrolment, naming and merging.

This is the only module that mixes DeepFace with the ORM. `face_recognition`
stays ORM-free and speaks in identity *codes*; everything here translates those
codes into `Person` rows.

Auto-enrolment is deliberately conservative. An unmatched face becomes a
`FaceCandidate` first, and only becomes a `Person` after it has been seen in
`FaceCandidate.PROMOTE_AFTER_SIGHTINGS` separate events. A single false
detection would otherwise create a permanent phantom identity, and phantoms are
much more annoying to clean up than a missed enrolment.
"""

import logging
import os
import shutil
import tempfile
from contextlib import contextmanager

from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from notifications.models import Notification

from .face_recognition import (
    CANDIDATE_DB,
    PERSON_DB,
    crop_face,
    db_path,
    face_quality,
    invalidate_db_cache,
    search_faces,
)
from .models import FaceCandidate, Person, Sighting

logger = logging.getLogger(__name__)


@contextmanager
def _temp_jpeg(image_bytes):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name
        yield tmp_path
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def label_faces(faces):
    """Replace identity codes with display names for rendering."""
    codes = {face.get("code") for face in faces if face.get("code")}
    names = {
        person.code: person.display_name
        for person in Person.objects.filter(code__in=codes)
    }
    for face in faces:
        code = face.get("code")
        face["name"] = names.get(code, "Unknown") if code else "Unknown"
    return faces


def identify_faces(image_path, detected_boxes):
    """Match faces in a capture against enrolled people.

    Falls back to the detector's boxes when nothing is enrolled yet, so the
    bootstrap case (empty database) still yields candidates to enrol.
    """
    if Person.objects.exists():
        matches = search_faces(image_path, PERSON_DB)
        if matches:
            return matches

    return [
        {"code": None, "matched": False, "distance": None, "threshold": None, "box": box}
        for box in detected_boxes
    ]


def register_sightings(event, matches, image_path):
    """Record who was seen, and enrol whoever was not.

    Returns (sightings, promoted_people). Existing sightings for the event are
    cleared first so re-analysing is idempotent.
    """
    event.sightings.all().delete()

    sightings = []
    promoted = []
    seen_person_ids = set()

    for match in matches:
        box = list(match["box"])

        if match.get("matched") and match.get("code"):
            person = Person.objects.filter(code=match["code"]).first()
            if person is None:
                logger.warning("Match on unknown code %s; treating as unmatched", match["code"])
            else:
                if person.pk not in seen_person_ids:
                    sightings.append(
                        Sighting.objects.create(
                            event=event,
                            person=person,
                            distance=match.get("distance"),
                            box=box,
                        )
                    )
                    seen_person_ids.add(person.pk)
                Person.objects.filter(pk=person.pk).update(last_seen_at=event.detected_at)
                continue

        # Unmatched: only good-quality faces are worth remembering.
        quality = face_quality(image_path, match["box"])
        if not quality["usable"]:
            logger.info(
                "Skipping unmatched face on event %s: %sx%s sharpness %.1f",
                event.pk,
                quality["width"],
                quality["height"],
                quality["sharpness"],
            )
            continue

        person = observe_candidate(image_path, match["box"], event)
        if person and person.pk not in seen_person_ids:
            promoted.append(person)
            sightings.append(
                Sighting.objects.create(
                    event=event,
                    person=person,
                    distance=match.get("distance"),
                    box=box,
                    is_enrollment=True,
                )
            )
            seen_person_ids.add(person.pk)

    return sightings, promoted


def observe_candidate(image_path, box, event):
    """Track an unmatched face. Returns a Person if this sighting promoted it."""
    crop = crop_face(image_path, box)
    if not crop:
        return None

    candidate = _find_candidate(crop)

    if candidate is None:
        candidate = FaceCandidate(
            camera=event.camera,
            sighting_count=1,
            first_seen_at=event.detected_at,
            last_seen_at=event.detected_at,
        )
        candidate.image.save(f"{timezone.now():%Y%m%d-%H%M%S-%f}.jpg", ContentFile(crop), save=True)
        invalidate_db_cache(CANDIDATE_DB)
        logger.info("New face candidate %s from event %s", candidate.code, event.pk)
        return None

    candidate.sighting_count += 1
    candidate.last_seen_at = event.detected_at
    candidate.save(update_fields=["sighting_count", "last_seen_at"])

    if not candidate.ready_to_promote:
        logger.info(
            "Candidate %s now at %s sighting(s)", candidate.code, candidate.sighting_count
        )
        return None

    return promote_candidate(candidate, crop, seen_at=event.detected_at)


def _find_candidate(crop_bytes):
    """Search the candidate database for this face."""
    if not FaceCandidate.objects.exists():
        return None

    with _temp_jpeg(crop_bytes) as crop_path:
        matches = search_faces(crop_path, CANDIDATE_DB)

    for match in matches:
        if match.get("matched") and match.get("code"):
            candidate = FaceCandidate.objects.filter(code=match["code"]).first()
            if candidate:
                return candidate
    return None


@transaction.atomic
def promote_candidate(candidate, crop_bytes=None, seen_at=None):
    """Turn a candidate into an unnamed Person and notify the user."""
    if crop_bytes is None:
        candidate.image.open("rb")
        try:
            crop_bytes = candidate.image.read()
        finally:
            candidate.image.close()

    person = Person(
        auto_created=True,
        last_seen_at=seen_at or candidate.last_seen_at or timezone.now(),
    )
    person.reference_image.save(
        f"{timezone.now():%Y%m%d-%H%M%S-%f}.jpg", ContentFile(crop_bytes), save=True
    )

    candidate_code = candidate.code
    candidate.image.delete(save=False)
    candidate.delete()
    _remove_identity_dir(CANDIDATE_DB, candidate_code)
    invalidate_db_cache(CANDIDATE_DB)
    invalidate_db_cache(PERSON_DB)

    Notification.objects.create(
        kind=Notification.Kind.NEW_PERSON,
        title="New face detected",
        message=(
            f"A new face was seen more than once and added to the database as "
            f"{person.display_name}. Give this person a name so future sightings are labelled."
        ),
        url=reverse("devices:person_name", args=[person.pk]),
    )
    logger.info("Promoted candidate %s to person %s", candidate_code, person.code)
    return person


def _remove_identity_dir(db_name, code):
    directory = db_path(db_name, ensure=False) / code
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)


@transaction.atomic
def merge_persons(source, target):
    """Fold `source` into `target`: move references, repoint sightings, delete source.

    Needed because threshold matching creates several identities for the same
    face across angles and lighting.
    """
    if source.pk == target.pk:
        raise ValueError("Cannot merge a person into themselves")

    target_dir = db_path(PERSON_DB) / target.code
    target_dir.mkdir(parents=True, exist_ok=True)
    source_dir = db_path(PERSON_DB, ensure=False) / source.code

    if source_dir.exists():
        for image in source_dir.iterdir():
            if not image.is_file():
                continue
            destination = target_dir / image.name
            counter = 1
            while destination.exists():
                destination = target_dir / f"{image.stem}-{counter}{image.suffix}"
                counter += 1
            shutil.move(str(image), str(destination))

    # Sightings for events the target already appears in would break the
    # per-event uniqueness constraint, so drop those rather than move them.
    duplicate_events = set(
        Sighting.objects.filter(person=target).values_list("event_id", flat=True)
    )
    Sighting.objects.filter(person=source, event_id__in=duplicate_events).delete()
    Sighting.objects.filter(person=source).update(person=target)

    if source.last_seen_at and (not target.last_seen_at or source.last_seen_at > target.last_seen_at):
        target.last_seen_at = source.last_seen_at
        target.save(update_fields=["last_seen_at"])

    source_code = source.code
    source.delete()
    _remove_identity_dir(PERSON_DB, source_code)
    invalidate_db_cache(PERSON_DB)
    logger.info("Merged person %s into %s", source_code, target.code)
    return target


def name_person(person, name):
    """Label a person and mark any prompts about them as handled."""
    person.name = name.strip()
    person.save(update_fields=["name", "updated_at"])

    Notification.objects.filter(
        kind=Notification.Kind.NEW_PERSON,
        url=reverse("devices:person_name", args=[person.pk]),
        is_read=False,
    ).update(is_read=True, read_at=timezone.now())
    return person


def approve_sighting(sighting):
    """User confirmed the match is correct."""
    sighting.confirmed = True
    sighting.save(update_fields=["confirmed"])
    return sighting


def reject_sighting(sighting):
    """Match was wrong: remove it and give the real face its own shot at identity.

    The face may be genuine even when the match is not, so a usable crop goes
    back through the candidate pipeline — a second sighting makes it a person.
    The event's stored `recognized`/`faces` snapshot and the person's
    `last_seen_at` are rebuilt from the sightings that remain.
    """
    event = sighting.event
    person = sighting.person
    box = sighting.box
    sighting.delete()

    if box:
        event.image.open("rb")
        try:
            image_bytes = event.image.read()
        finally:
            event.image.close()
        with _temp_jpeg(image_bytes) as image_path:
            if face_quality(image_path, box)["usable"]:
                observe_candidate(image_path, box, event)

    for face in event.faces:
        if face.get("code") == person.code:
            face["code"] = None
            face["name"] = "Unknown"
            face["distance"] = None
    event.recognized = sorted(
        {s.person.display_name for s in event.sightings.select_related("person")}
    )
    event.save(update_fields=["faces", "recognized"])

    person.last_seen_at = (
        person.sightings.order_by("-event__detected_at")
        .values_list("event__detected_at", flat=True)
        .first()
    )
    person.save(update_fields=["last_seen_at"])


def person_references(person):
    """Filenames of every reference photo in persons/<code>/, newest first.

    The directory itself is the DeepFace database — every file in it is an
    active reference, so more angles means better matching.
    """
    directory = db_path(PERSON_DB, ensure=False) / person.code
    if not directory.exists():
        return []
    return sorted((f.name for f in directory.iterdir() if f.is_file()), reverse=True)


def add_person_reference(person, image_file):
    """Store an uploaded photo under persons/<code>/ and make it the primary.

    `reference_image.save` routes through `_person_upload_to`, so the file lands
    in the person's directory and the field points at the newest upload. Older
    files stay in the directory and keep working as extra references.
    """
    person.reference_image.save(image_file.name, image_file, save=True)
    invalidate_db_cache(PERSON_DB)
    return person


def delete_person_reference(person, filename):
    """Remove one reference photo. Refuses to remove the last one — a person
    with no references can never match anything."""
    directory = (db_path(PERSON_DB, ensure=False) / person.code).resolve()
    target = (directory / filename).resolve()
    if not str(target).startswith(str(directory) + os.sep) or not target.is_file():
        raise ValueError("Not a reference of this person")

    remaining = [name for name in person_references(person) if name != filename]
    if not remaining:
        raise ValueError("Cannot remove the last reference — delete the person instead")

    target.unlink()
    invalidate_db_cache(PERSON_DB)

    # If the primary was deleted, point the field at the newest remaining file.
    if person.reference_image.name.endswith(f"/{filename}"):
        person.reference_image.name = f"persons/{person.code}/{remaining[0]}"
        person.save(update_fields=["reference_image"])


def delete_person(person):
    """Remove a person and their reference directory."""
    code = person.code
    person.reference_image.delete(save=False)
    person.delete()
    _remove_identity_dir(PERSON_DB, code)
    invalidate_db_cache(PERSON_DB)


def delete_candidate(candidate):
    """Remove a face candidate and its reference directory."""
    code = candidate.code
    candidate.image.delete(save=False)
    candidate.delete()
    _remove_identity_dir(CANDIDATE_DB, code)
    invalidate_db_cache(CANDIDATE_DB)
