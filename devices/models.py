from django.db import models


class NVR(models.Model):
    """Network Video Recorder — central hub for camera feeds."""

    name = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(default=554)
    username = models.CharField(max_length=100, blank=True)
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
