import os

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from devices.models import Camera, NVR, Sensor


class Command(BaseCommand):
    help = "Initialize the database with an admin user and sample devices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=os.environ.get("ADMIN_USERNAME", "admin"),
        )
        parser.add_argument(
            "--password",
            default=os.environ.get("ADMIN_PASSWORD", "admin"),
        )
        parser.add_argument(
            "--no-sample-data",
            action="store_true",
            help="Skip creating sample NVR/camera/sensor records.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        password = options["password"]

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"User '{username}' already exists — skipping.")
        else:
            User.objects.create_superuser(username=username, password=password)
            self.stdout.write(self.style.SUCCESS(f"Created admin user: {username}"))

        if options["no_sample_data"]:
            return

        if NVR.objects.exists():
            self.stdout.write("Sample data already exists — skipping.")
            return

        nvr = NVR.objects.create(
            name="Main NVR",
            ip_address="192.168.1.100",
            port=554,
            location="Server room",
            is_online=True,
        )

        Camera.objects.bulk_create([
            Camera(name="Front entrance", nvr=nvr, channel=1, location="Main door", status=Camera.Status.ONLINE),
            Camera(name="Parking lot", nvr=nvr, channel=2, location="North side", status=Camera.Status.RECORDING),
            Camera(name="Warehouse", nvr=nvr, channel=3, location="Building B", status=Camera.Status.OFFLINE),
        ])

        Sensor.objects.bulk_create([
            Sensor(name="Front door contact", sensor_type=Sensor.SensorType.DOOR, location="Main entrance"),
            Sensor(name="Warehouse motion", sensor_type=Sensor.SensorType.MOTION, location="Building B"),
            Sensor(name="Server room smoke", sensor_type=Sensor.SensorType.SMOKE, location="Server room"),
        ])

        self.stdout.write(self.style.SUCCESS("Sample devices created."))
