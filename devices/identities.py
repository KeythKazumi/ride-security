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
    MIN_VERIFY_CONFIDENCE,
    PERSON_DB,
    crop_face,
    db_path,
    face_belongs_to_person,
    face_quality,
    invalidate_db_cache,
    search_faces,
    verify_face_crop,
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


def _unmatched(box):
    return {"code": None, "matched": False, "distance": None, "threshold": None, "box": box}


def identify_faces(image_path, detected_boxes):
    """Match faces in a capture against enrolled people, one crop at a time.

    Every detected face comes back exactly once, in order. Each face is cut
    out with a margin and upscaled to the embedding model's input size before
    searching — a 45px face on a street camera embedded straight from the
    1080p frame is mostly noise, and DeepFace would otherwise silently omit
    it from the results (it only reports faces under the distance threshold),
    dropping it before it could become a candidate.
    """
    if not Person.objects.exists():
        return [_unmatched(tuple(box)) for box in detected_boxes]

    results = []
    for box in detected_boxes:
        crop = crop_face(image_path, box, margin=0.5, upscale=True)
        match = _search_crop(crop, PERSON_DB) if crop else None
        results.append({**match, "box": tuple(box)} if match else _unmatched(tuple(box)))
    return results


def _search_crop(crop_bytes, db_name):
    """Best match for a single face crop, or None."""
    with _temp_jpeg(crop_bytes) as crop_path:
        matches = search_faces(crop_path, db_name, is_crop=True)
    matched = [m for m in matches if m.get("matched") and m.get("code")]
    return min(matched, key=lambda m: m["distance"]) if matched else None


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

        # Unmatched: only good-quality faces on an actual person are worth
        # remembering. The Haar cascade fires on floor tiles and window frames;
        # a real face sits on top of a detected human shape.
        if not face_belongs_to_person(match["box"], event.persons or []):
            logger.info(
                "Skipping unmatched face on event %s: box %s is not on a detected person",
                event.pk,
                box,
            )
            continue
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
    crop = crop_face(image_path, box, upscale=True)
    if not crop:
        return None

    confidence = verify_face_crop(crop)
    if confidence < MIN_VERIFY_CONFIDENCE:
        logger.info(
            "Skipping face on event %s: verification confidence %.2f < %.2f",
            event.pk,
            confidence,
            MIN_VERIFY_CONFIDENCE,
        )
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

    match = _search_crop(crop_bytes, CANDIDATE_DB)
    return FaceCandidate.objects.filter(code=match["code"]).first() if match else None


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
            if (
                face_belongs_to_person(box, event.persons or [])
                and face_quality(image_path, box)["usable"]
            ):
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


def purge_bad_references():
    """Delete auto-created people and candidates whose image is not a face crop.

    Before the whole-frame guard and MTCNN verification existed, a frame-sized
    "face" or a patch of floor tile could be enrolled. Such a reference matches
    everything loosely and hijacks every unmatched sighting, so it has to go.
    Only auto-created rows are touched — a hand-uploaded reference is the
    operator's call. Returns (persons_removed, candidates_removed).
    """
    def has_face(field):
        try:
            field.open("rb")
            try:
                return verify_face_crop(field.read()) >= MIN_VERIFY_CONFIDENCE
            finally:
                field.close()
        except (OSError, ValueError):
            return False

    persons = [p for p in Person.objects.filter(auto_created=True) if not has_face(p.reference_image)]
    for person in persons:
        logger.warning("Purging person %s: reference is not a face", person.code)
        delete_person(person)

    candidates = [c for c in FaceCandidate.objects.all() if not has_face(c.image)]
    for candidate in candidates:
        logger.warning("Purging candidate %s: image is not a face", candidate.code)
        delete_candidate(candidate)

    return len(persons), len(candidates)


def delete_candidate(candidate):
    """Remove a face candidate and its reference directory."""
    code = candidate.code
    candidate.image.delete(save=False)
    candidate.delete()
    _remove_identity_dir(CANDIDATE_DB, code)
    invalidate_db_cache(CANDIDATE_DB)
