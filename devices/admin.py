from django.contrib import admin

from .models import Camera, FaceCandidate, MotionEvent, NVR, Person, Sensor, Sighting


@admin.register(NVR)
class NVRAdmin(admin.ModelAdmin):
    list_display = ("name", "ip_address", "get_snapshot_port", "get_rtsp_port", "location", "is_online", "updated_at")
    list_filter = ("is_online",)
    search_fields = ("name", "ip_address", "location")
    fieldsets = (
        (None, {
            "fields": ("name", "ip_address", "channel_count", "location", "username", "password", "is_online"),
        }),
        ("Ports", {
            "fields": ("port", "https_port", "management_port", "service_port", "remote_stream_port", "rtsp_port", "openapi_port", "use_https"),
            "description": "Leave a port blank to use the default/fallback. Snapshot URL uses openapi_port → https_port (with HTTPS) → management_port.",
        }),
    )

    @admin.display(description="Snapshot port")
    def get_snapshot_port(self, obj):
        return obj.get_snapshot_port()

    @admin.display(description="RTSP port")
    def get_rtsp_port(self, obj):
        return obj.get_rtsp_port()


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ("name", "nvr", "channel", "location", "status", "show_image", "updated_at")
    list_filter = ("status", "nvr", "show_image")
    search_fields = ("name", "location")
    fieldsets = (
        (None, {
            "fields": ("name", "nvr", "channel", "location", "status", "show_image"),
        }),
        ("Connection", {
            "fields": ("rtsp_url", "snapshot_url"),
            "description": "Leave blank to auto-generate from the NVR credentials.",
        }),
    )


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("display_name", "code", "auto_created", "sighting_count", "last_seen_at", "created_at")
    list_filter = ("auto_created",)
    search_fields = ("name", "code")
    readonly_fields = ("code", "created_at", "updated_at")

    @admin.display(description="Sightings")
    def sighting_count(self, obj):
        return obj.sightings.count()


@admin.register(FaceCandidate)
class FaceCandidateAdmin(admin.ModelAdmin):
    list_display = ("code", "camera", "sighting_count", "first_seen_at", "last_seen_at")
    list_filter = ("camera",)
    readonly_fields = ("code", "first_seen_at", "last_seen_at")


@admin.register(Sighting)
class SightingAdmin(admin.ModelAdmin):
    list_display = ("person", "event", "distance", "is_enrollment", "created_at")
    list_filter = ("is_enrollment", "person")
    readonly_fields = ("created_at",)


@admin.register(MotionEvent)
class MotionEventAdmin(admin.ModelAdmin):
    list_display = ("id", "camera", "detected_at", "source", "status", "quality", "face_count", "best_face_size", "blur_score", "reviewed_at")
    list_filter = ("status", "quality", "source", "camera")
    readonly_fields = ("detected_at", "analyzed_at", "faces", "recognized", "reviewed_at")
    date_hierarchy = "detected_at"

    @admin.display(description="Best face")
    def best_face_size(self, obj):
        return obj.best_face_size or "—"


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = ("name", "sensor_type", "location", "status", "updated_at")
    list_filter = ("sensor_type", "status")
    search_fields = ("name", "location")
