import time

from django.core.management.base import BaseCommand

from devices.events import analyze_event, process_pending
from devices.models import MotionEvent


class Command(BaseCommand):
    help = "Run face detection, quality scoring and recognition on pending motion events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of events to process per pass.",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Keep polling for pending events instead of exiting after one pass.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=5.0,
            help="Seconds between passes when --loop is set (default: 5).",
        )
        parser.add_argument(
            "--reanalyze",
            action="store_true",
            help=(
                "Also re-run events already analyzed. Use after changing the recognition "
                "pipeline, since analysis is otherwise a one-shot per capture."
            ),
        )
        parser.add_argument(
            "--camera",
            type=int,
            action="append",
            dest="cameras",
            help="Restrict to these camera IDs. Repeatable.",
        )

    def handle(self, *args, **options):
        if options["reanalyze"]:
            self._reanalyze(options["limit"], options["cameras"])
            return

        if options["loop"]:
            self.stdout.write("Processing pending events. Press Ctrl-C to stop.")
            try:
                while True:
                    self._pass(options["limit"])
                    time.sleep(options["interval"])
            except KeyboardInterrupt:
                self.stdout.write("\nStopped.")
            return

        processed = self._pass(options["limit"])
        if not processed:
            self.stdout.write("No pending events.")

    def _reanalyze(self, limit, cameras):
        """Re-run analysis over existing captures, newest first."""
        queryset = MotionEvent.objects.select_related("camera").order_by("-detected_at")
        if cameras:
            queryset = queryset.filter(camera_id__in=cameras)
        if limit:
            queryset = queryset[:limit]

        events = list(queryset)
        if not events:
            self.stdout.write("No events to re-analyze.")
            return

        self.stdout.write(f"Re-analyzing {len(events)} event(s)...")
        for event in events:
            self._report(analyze_event(event))

    def _pass(self, limit):
        events = process_pending(limit=limit)
        for event in events:
            self._report(event)
        return events

    def _report(self, event):
        if event.status == MotionEvent.Status.FAILED:
            self.stderr.write(self.style.ERROR(f"{event.pk}: failed — {event.error}"))
            return

        style = self.style.SUCCESS if event.is_usable else self.style.WARNING
        detail = f"{event.pk}: {event.camera.name} — {event.get_quality_display()}"
        if event.face_count:
            detail += f", {event.face_count} face(s), best {event.best_face_size}"
            if event.blur_score is not None:
                detail += f", sharpness {event.blur_score:.1f}"
        if event.recognized:
            detail += f", recognized: {event.recognized_display}"
        self.stdout.write(style(detail))
