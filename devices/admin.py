from django.contrib import admin

from .models import Camera, NVR, Sensor


@admin.register(NVR)
class NVRAdmin(admin.ModelAdmin):
    list_display = ("name", "ip_address", "location", "is_online", "updated_at")
    list_filter = ("is_online",)
    search_fields = ("name", "ip_address", "location")


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ("name", "nvr", "channel", "location", "status", "updated_at")
    list_filter = ("status", "nvr")
    search_fields = ("name", "location")


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = ("name", "sensor_type", "location", "status", "updated_at")
    list_filter = ("sensor_type", "status")
    search_fields = ("name", "location")
