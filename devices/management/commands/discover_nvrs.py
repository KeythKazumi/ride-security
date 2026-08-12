from django.core.management.base import BaseCommand

from devices.discovery import discover_devices, get_local_networks


class Command(BaseCommand):
    help = "Scan the local network for NVRs and IP cameras."

    def add_arguments(self, parser):
        parser.add_argument(
            "--network",
            help="CIDR to scan, e.g. 192.168.1.0/24. Defaults to the detected local networks.",
        )
        parser.add_argument(
            "--no-scan",
            action="store_true",
            help="Only run the ONVIF multicast probe, skip the TCP port sweep.",
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=4.0,
            help="Seconds to wait for ONVIF replies (default: 4).",
        )

    def handle(self, *args, **options):
        networks = [options["network"]] if options["network"] else get_local_networks()
        self.stdout.write(f"Scanning: {', '.join(str(net) for net in networks)}")

        devices = discover_devices(
            network=options["network"],
            onvif_timeout=options["timeout"],
            include_scan=not options["no_scan"],
        )

        if not devices:
            self.stdout.write(self.style.WARNING("No devices found."))
            return

        for device in devices:
            self.stdout.write(self.style.SUCCESS(device["ip_address"]))
            self.stdout.write(f"  detected via: {device['source']}")
            if device["open_ports"]:
                ports = ", ".join(str(port) for port in device["open_ports"])
                self.stdout.write(f"  open ports:   {ports}")
            if device["suggested"]:
                mapping = ", ".join(f"{k}={v}" for k, v in device["suggested"].items())
                self.stdout.write(f"  suggested:    {mapping}")
            for url in device["service_urls"]:
                self.stdout.write(f"  service url:  {url}")

        self.stdout.write(f"\n{len(devices)} device(s) found.")
