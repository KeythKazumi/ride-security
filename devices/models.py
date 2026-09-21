import uuid
from urllib.parse import quote, urlencode

from django.db import models


class NVR(models.Model):
    """Network Video Recorder — central hub for camera feeds."""

    name = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField()
    channel_count = models.PositiveIntegerField(
        default=4,
        choices=[(4, "4"), (8, "8"), (10, "10"), (16, "16")],
        help_text="Number of channels available on this NVR.",
    )
    port = models.PositiveIntegerField(default=554, help_text="Legacy / fallback port.")
    https_port = models.PositiveIntegerField(null=True, blank=True, help_text="HTTPS web port, often 443.")
    service_port = models.PositiveIntegerField(null=True, blank=True, help_text="Cloud / service port.")
    management_port = models.PositiveIntegerField(null=True, blank=True, help_text="HTTP management port, often 80.")
    remote_stream_port = models.PositiveIntegerField(null=True, blank=True, help_text="Port used for remote video streams.")
    rtsp_port = models.PositiveIntegerField(null=True, blank=True, help_text="RTSP port, often 554.")
    openapi_port = models.PositiveIntegerField(null=True, blank=True, help_text="Open API / SDK port for snapshots and control.")
    use_https = models.BooleanField(default=False, help_text="Use HTTPS for snapshot requests when possible.")
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=200, blank=True)
    is_online = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "NVR"
        verbose_name_plural = "NVRs"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_rtsp_port(self):
        return self.rtsp_port or self.remote_stream_port or self.port or 554

    def get_snapshot_port(self):
        """Return the port used for JPEG snapshot requests."""
        if self.openapi_port:
            return self.openapi_port
        if self.use_https and self.https_port:
            return self.https_port
        return self.management_port or self.port or 80

    def get_snapshot_protocol(self):
        if self.openapi_port:
            return "https" if self.use_https else "http"
        if self.use_https and self.https_port:
            return "https"
        return "http"

    def rtsp_userinfo(self):
        """Return the `user:pass@` prefix, percent-encoded.

        NVR passwords routinely contain `@`, `:` and `/`, which are all URL
        delimiters. Interpolating them raw produces a URL that either points at
        the wrong host or drops half the password.
        """
        if not self.username:
            return ""
        user = quote(self.username, safe="")
        password = quote(self.password, safe="")
        return f"{user}:{password}@" if self.password else f"{user}@"

    def rtsp_base_url(self):
        """Return a base RTSP URL for the NVR (VIGI format)."""
        return f"rtsp://{self.rtsp_userinfo()}{self.ip_address}:{self.get_rtsp_port()}/live/1/1/avm"


class Camera(models.Model):
    """IP camera connected to an NVR."""

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        RECORDING = "recording", "Recording"

    name = models.CharField(max_length=100)
    nvr = models.ForeignKey(
        NVR,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cameras",
    )
    channel = models.PositiveIntegerField(default=1)
    location = models.CharField(max_length=200, blank=True)
    rtsp_url = models.CharField(max_length=500, blank=True)
    snapshot_url = models.CharField(max_length=500, blank=True, help_text="Override URL for fetching a JPEG snapshot. If blank, a TP-Link VIGI default is used.")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OFFLINE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_rtsp_url(self):
        """Return RTSP stream URL. Uses override if set, otherwise builds from NVR (VIGI format)."""
        if self.rtsp_url:
            return self.rtsp_url
        if self.nvr:
            port = self.nvr.get_rtsp_port()
            auth = self.nvr.rtsp_userinfo()
            return f"rtsp://{auth}{self.nvr.ip_address}:{port}/live/{self.channel}/1/avm"
        return ""

    def get_snapshot_url(self):
        """Return HTTP snapshot URL. Uses override if set, otherwise builds from NVR."""
        if self.snapshot_url:
            return self.snapshot_url
        if self.nvr:
            protocol = self.nvr.get_snapshot_protocol()
            port = self.nvr.get_snapshot_port()
            query = urlencode(
                {
                    "channel": self.channel,
                    "user": self.nvr.username,
                    "pwd": self.nvr.password,
                }
            )
            return f"{protocol}://{self.nvr.ip_address}:{port}/cgi-bin/snapshot.cgi?{query}"
        return ""


class Sensor(models.Model):
    """Physical sensor (motion, door, smoke, etc.)."""

    class SensorType(models.TextChoices):
        MOTION = "motion", "Motion"
        DOOR = "door", "Door / Contact"
        SMOKE = "smoke", "Smoke"
        GLASS = "glass", "Glass Break"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        NORMAL = "normal", "Normal"
        TRIGGERED = "triggered", "Triggered"
        OFFLINE = "offline", "Offline"
        LOW_BATTERY = "low_battery", "Low Battery"

    name = models.CharField(max_length=100)
    sensor_type = models.CharField(
        max_length=20,
        choices=SensorType.choices,
        default=SensorType.MOTION,
    )
    location = models.CharField(max_length=200, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NORMAL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


def new_identity_code():
    return uuid.uuid4().hex[:12]


def _person_upload_to(instance, filename):
    # Keyed on the immutable code, never the name: DeepFace derives identity from
    # the directory name, so a rename must not move (or orphan) the references.
    return f"persons/{instance.code}/{filename}"


class Person(models.Model):
    """A face identity. Either enrolled by hand or auto-created from a capture.

    `name` is blank until a human labels the person, so the recognizer can build
    a face database on its own and ask for names afterwards.
    """

    code = models.CharField(
        max_length=32,
        unique=True,
        default=new_identity_code,
        editable=False,
        help_text="Immutable key used as the reference directory name.",
    )
    name = models.CharField(max_length=100, blank=True)
    reference_image = models.ImageField(
        upload_to=_person_upload_to,
        help_text="Clear front-facing photo. DeepFace will compare camera frames to this image.",
    )
    auto_created = models.BooleanField(
        default=False,
        help_text="Created by the recognizer from an unmatched face rather than uploaded.",
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "-created_at"]

    def __str__(self):
        return self.display_name

    @property
    def is_named(self):
        return bool(self.name.strip())

    @property
    def display_name(self):
        return self.name.strip() or f"Unnamed #{self.code[:6]}"


def _candidate_upload_to(instance, filename):
    return f"candidates/{instance.code}/{filename}"


class FaceCandidate(models.Model):
    """An unmatched face waiting to be seen again before it becomes a Person.

    A single detection is not enough evidence to create an identity — one
    false positive would permanently pollute the database. A candidate is
    promoted only after `PROMOTE_AFTER_SIGHTINGS` separate events.
    """

    PROMOTE_AFTER_SIGHTINGS = 2

    code = models.CharField(max_length=32, unique=True, default=new_identity_code, editable=False)
    image = models.ImageField(upload_to=_candidate_upload_to)
    camera = models.ForeignKey(
        Camera,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="face_candidates",
    )
    sighting_count = models.PositiveIntegerField(default=1)
    # Both are the event's detection time, not wall-clock save time — a backlog
    # re-analysis must not make an old face look like it was seen today.
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()

    class Meta:
        ordering = ["-last_seen_at"]

    def __str__(self):
        return f"Candidate {self.code[:6]} ({self.sighting_count} sighting(s))"

    @property
    def ready_to_promote(self):
        return self.sighting_count >= self.PROMOTE_AFTER_SIGHTINGS


class Sighting(models.Model):
    """One recognised face in one capture."""

    event = models.ForeignKey("MotionEvent", on_delete=models.CASCADE, related_name="sightings")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="sightings")
    distance = models.FloatField(null=True, blank=True, help_text="Cosine distance to the reference.")
    box = models.JSONField(default=list, blank=True)
    is_enrollment = models.BooleanField(
        default=False,
        help_text="This capture is the one that created the person.",
    )
    confirmed = models.BooleanField(
        null=True,
        blank=True,
        help_text="User verdict on the match. Null = unreviewed, True = approved.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["event", "person"], name="unique_sighting_per_event"),
        ]

    def __str__(self):
        return f"{self.person} in event {self.event_id}"


def _event_upload_to(instance, filename):
    return f"events/camera_{instance.camera_id}/{filename}"


class MotionEvent(models.Model):
    """A frame captured because motion was detected on a camera.

    Capture and analysis are deliberately separate: the detector only persists
    the frame (cheap), and face detection / recognition runs afterwards via
    `manage.py process_events` (expensive).
    """

    class Source(models.TextChoices):
        FRAME_DIFF = "frame_diff", "OpenCV frame diff"
        MANUAL = "manual", "Manual capture"
        ONVIF = "onvif", "ONVIF event"
        NVR_PUSH = "nvr_push", "NVR OpenAPI push"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending analysis"
        ANALYZED = "analyzed", "Analyzed"
        FAILED = "failed", "Analysis failed"

    class Quality(models.TextChoices):
        UNKNOWN = "unknown", "Not analyzed"
        USABLE = "usable", "Usable for recognition"
        PERSON = "person", "Person seen, no usable face"
        NO_FACE = "no_face", "No face detected"
        TOO_SMALL = "too_small", "Face too small"
        TOO_BLURRY = "too_blurry", "Face too blurry"

    camera = models.ForeignKey(
        Camera,
        on_delete=models.CASCADE,
        related_name="motion_events",
    )
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.FRAME_DIFF)
    image = models.ImageField(upload_to=_event_upload_to)
    detected_at = models.DateTimeField(auto_now_add=True)
    motion_score = models.FloatField(
        null=True,
        blank=True,
        help_text="Fraction of the frame that changed (0-1). Empty for manual captures.",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    analyzed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    face_count = models.PositiveIntegerField(default=0)
    best_face_width = models.PositiveIntegerField(default=0)
    best_face_height = models.PositiveIntegerField(default=0)
    blur_score = models.FloatField(
        null=True,
        blank=True,
        help_text="Laplacian variance of the largest face crop. Higher is sharper.",
    )
    quality = models.CharField(max_length=20, choices=Quality.choices, default=Quality.UNKNOWN)
    faces = models.JSONField(default=list, blank=True, help_text="Detected face boxes and labels.")
    recognized = models.JSONField(default=list, blank=True, help_text="Names matched against Person references.")
    persons = models.JSONField(
        default=list,
        blank=True,
        help_text="Person-shaped regions found by pedestrian detection (box x, y, w, h).",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a user confirmed the verdict. Confirmed no-face events are purged by cleanup_events.",
    )

    class Meta:
        ordering = ["-detected_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["-detected_at"]),
        ]

    def __str__(self):
        return f"{self.camera} @ {self.detected_at:%Y-%m-%d %H:%M:%S}"

    @property
    def is_usable(self):
        return self.quality == self.Quality.USABLE

    @property
    def is_reviewed(self):
        return self.reviewed_at is not None

    @property
    def best_face_size(self):
        if not self.best_face_width or not self.best_face_height:
            return None
        return f"{self.best_face_width}x{self.best_face_height}"

    @property
    def recognized_display(self):
        return ", ".join(self.recognized) if self.recognized else "—"
