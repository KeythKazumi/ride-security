import time

from django.core.management.base import BaseCommand

from devices.events import cleanup_candidates, cleanup_reviewed_events

SIX_HOURS = 6 * 60 * 60


class Command(BaseCommand):
    help = (
        "Delete analyzed 'no face' events that a user has confirmed, freeing the "
        "storage their frames occupy."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Keep running a cleanup pass every --interval seconds.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=SIX_HOURS,
            help="Seconds between passes when --loop is set (default: 21600 = 6h).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting anything.",
        )

    def handle(self, *args, **options):
        if not options["loop"]:
            self._run(options["dry_run"])
            return

        self.stdout.write(
            f"Cleaning confirmed no-face events every {options['interval'] / 3600:g}h. "
            "Press Ctrl-C to stop."
        )
        try:
            while True:
                self._run(options["dry_run"])
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("\nStopped.")

    def _run(self, dry_run):
        if dry_run:
            count = cleanup_candidates().count()
            self.stdout.write(f"{count} confirmed no-face event(s) would be removed.")
            return

        removed, freed = cleanup_reviewed_events()
        if removed:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Removed {removed} confirmed no-face event(s), freed {freed / 1024:.0f} KB."
                )
            )
        else:
            self.stdout.write("Nothing confirmed for cleanup.")
