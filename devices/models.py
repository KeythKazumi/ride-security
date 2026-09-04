from django.db import models


class NVR(models.Model):
    """Network Video Recorder — central hub for camera feeds."""

    name = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField()
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

    def rtsp_base_url(self):
        """Return a base RTSP URL for the NVR (VIGI format)."""
        port = self.get_rtsp_port()
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"rtsp://{auth}{self.ip_address}:{port}/live/1/1/avm"


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
            auth = f"{self.nvr.username}:{self.nvr.password}@" if self.nvr.username else ""
            return f"rtsp://{auth}{self.nvr.ip_address}:{port}/live/{self.channel}/1/avm"
        return ""

    def get_snapshot_url(self):
        """Return HTTP snapshot URL. Uses override if set, otherwise builds from NVR."""
        if self.snapshot_url:
            return self.snapshot_url
        if self.nvr:
            protocol = self.nvr.get_snapshot_protocol()
            port = self.nvr.get_snapshot_port()
            return (
                f"{protocol}://{self.nvr.ip_address}:{port}/cgi-bin/snapshot.cgi?"
                f"channel={self.channel}&user={self.nvr.username}&pwd={self.nvr.password}"
            )
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
