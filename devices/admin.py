from django.contrib import admin

from .models import Camera, NVR, Sensor


@admin.register(NVR)
class NVRAdmin(admin.ModelAdmin):
    list_display = ("name", "ip_address", "get_snapshot_port", "get_rtsp_port", "location", "is_online", "updated_at")
    list_filter = ("is_online",)
    search_fields = ("name", "ip_address", "location")
    fieldsets = (
        (None, {
            "fields": ("name", "ip_address", "location", "username", "password", "is_online"),
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
    list_display = ("name", "nvr", "channel", "location", "status", "updated_at")
    list_filter = ("status", "nvr")
    search_fields = ("name", "location")
    fieldsets = (
        (None, {
            "fields": ("name", "nvr", "channel", "location", "status"),
        }),
        ("Connection", {
            "fields": ("rtsp_url", "snapshot_url"),
            "description": "Leave blank to auto-generate from the NVR credentials.",
        }),
    )


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = ("name", "sensor_type", "location", "status", "updated_at")
    list_filter = ("sensor_type", "status")
    search_fields = ("name", "location")
