from django import forms

from .models import Camera, NVR, Sensor


class NVRForm(forms.ModelForm):
    """Form for adding or editing an NVR from the frontend."""

    class Meta:
        model = NVR
        fields = [
            "name",
            "ip_address",
            "location",
            "username",
            "password",
            "port",
            "https_port",
            "service_port",
            "management_port",
            "remote_stream_port",
            "rtsp_port",
            "openapi_port",
            "use_https",
            "is_online",
        ]
        widgets = {
            "password": forms.PasswordInput(render_value=True),
        }


class CameraForm(forms.ModelForm):
    """Form for adding or editing a camera from the frontend."""

    class Meta:
        model = Camera
        fields = [
            "name",
            "nvr",
            "channel",
            "location",
            "rtsp_url",
            "snapshot_url",
            "status",
        ]


class SensorForm(forms.ModelForm):
    """Form for adding or editing a sensor from the frontend."""

    class Meta:
        model = Sensor
        fields = [
            "name",
            "sensor_type",
            "location",
            "ip_address",
            "status",
        ]
