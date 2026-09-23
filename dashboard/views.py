from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from devices.models import Camera, NVR, Sensor


@login_required
def home(request):
    cameras = Camera.objects.select_related("nvr")
    sensors = Sensor.objects.all()
    nvrs = NVR.objects.all()
    live_cameras = [camera for camera in cameras if camera.get_rtsp_url()]

    context = {
        "stats": {
            "cameras_total": cameras.count(),
            "cameras_online": cameras.filter(status=Camera.Status.ONLINE).count(),
            "sensors_total": sensors.count(),
            "sensors_triggered": sensors.filter(status=Sensor.Status.TRIGGERED).count(),
            "nvrs_total": nvrs.count(),
            "nvrs_online": nvrs.filter(is_online=True).count(),
        },
        "cameras": cameras[:6],
        "live_cameras": live_cameras,
        "sensors": sensors[:6],
        "nvrs": nvrs[:4],
    }
    return render(request, "dashboard/home.html", context)
