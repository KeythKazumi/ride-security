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
        parser.add_argument(
            "--all-empty",
            action="store_true",
            help=(
                "Also delete empty captures nobody has reviewed yet: analyzed, no face, "
                "no person detected. One-off purge; not for --loop."
            ),
        )

    def handle(self, *args, **options):
        if not options["loop"]:
            self._run(options["dry_run"], options["all_empty"])
            return

        self.stdout.write(
            f"Cleaning confirmed no-face events every {options['interval'] / 3600:g}h. "
            "Press Ctrl-C to stop."
        )
        try:
            while True:
                self._run(options["dry_run"], all_empty=False)
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("\nStopped.")

    def _run(self, dry_run, all_empty):
        queryset = cleanup_candidates(require_review=not all_empty)
        label = "empty" if all_empty else "confirmed no-face"
        if dry_run:
            self.stdout.write(f"{queryset.count()} {label} event(s) would be removed.")
            return

        removed, freed = cleanup_reviewed_events(queryset)
        if removed:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Removed {removed} {label} event(s), freed {freed / 1024:.0f} KB."
                )
            )
        else:
            self.stdout.write(f"Nothing {label} to clean up.")
