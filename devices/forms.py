from django import forms

from .models import Camera, NVR, Sensor


class NVRForm(forms.ModelForm):
    """Form for adding or editing an NVR from the frontend."""

    class Meta:
        model = NVR
        fields = [
            "name",
            "ip_address",
            "channel_count",
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

    channel = forms.TypedChoiceField(
        coerce=int,
        label="Channel",
        help_text="Select the channel on the parent NVR.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        nvr = None

        if self.data.get("nvr"):
            try:
                nvr = NVR.objects.get(pk=self.data["nvr"])
            except (NVR.DoesNotExist, ValueError):
                nvr = None
        elif self.instance and self.instance.pk and self.instance.nvr_id:
            nvr = self.instance.nvr

        max_channels = nvr.channel_count if nvr else 16
        self.fields["channel"].choices = [(i, str(i)) for i in range(1, max_channels + 1)]

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
