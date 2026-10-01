from django import forms

from .models import Camera, NVR, Person, Sensor


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
            "show_image",
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


class PersonNameForm(forms.ModelForm):
    """Name an auto-created person, or fold them into someone already named.

    Threshold matching produces several identities for the same face, so naming
    and merging belong in the same step: the moment you recognise a face is the
    moment you notice it is a duplicate.
    """

    merge_into = forms.ModelChoiceField(
        queryset=Person.objects.none(),
        required=False,
        label="Or merge into an existing person",
        help_text="Moves this face's references and sightings onto that person, then deletes this one.",
    )

    class Meta:
        model = Person
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        others = Person.objects.exclude(pk=self.instance.pk).exclude(name="").order_by("name")
        self.fields["merge_into"].queryset = others

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get("name") or "").strip()
        merge_into = cleaned.get("merge_into")

        if not name and not merge_into:
            raise forms.ValidationError("Enter a name, or pick a person to merge into.")
        if name and merge_into:
            raise forms.ValidationError("Do one or the other: name this person, or merge them.")

        cleaned["name"] = name
        return cleaned


class PersonForm(forms.ModelForm):
    """Manual enrolment from the frontend."""

    class Meta:
        model = Person
        fields = ["name", "reference_image"]
