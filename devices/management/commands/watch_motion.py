"""Long-running motion watcher.

Each camera gets its own thread, because `iter_motion_events` blocks on
`cap.read()`. Threads (not processes) are fine here: the work is either waiting
on the network or inside OpenCV, which releases the GIL.
"""

import threading

from django.core.management.base import BaseCommand, CommandError

from devices import motion
from devices.events import record_motion_event
from devices.models import Camera, MotionEvent


class Command(BaseCommand):
    help = "Watch camera RTSP streams for motion and capture a frame when it is detected."

    def add_arguments(self, parser):
        parser.add_argument(
            "--camera",
            type=int,
            action="append",
            dest="cameras",
            help="Camera ID to watch. Repeatable. Defaults to every camera with an RTSP URL.",
        )
        parser.add_argument(
            "--min-area",
            type=float,
            default=motion.DEFAULT_MIN_AREA_RATIO,
            help=f"Fraction of the frame that must change (default: {motion.DEFAULT_MIN_AREA_RATIO}).",
        )
        parser.add_argument(
            "--cooldown",
            type=float,
            default=motion.DEFAULT_COOLDOWN,
            help=f"Seconds between captures per camera (default: {motion.DEFAULT_COOLDOWN}).",
        )
        parser.add_argument(
            "--sample-fps",
            type=float,
            default=motion.DEFAULT_SAMPLE_FPS,
            help=f"Frames per second to analyse (default: {motion.DEFAULT_SAMPLE_FPS}).",
        )
        parser.add_argument(
            "--analyze",
            action="store_true",
            help="Run face analysis inline instead of leaving events pending. Slower; blocks detection.",
        )

    def handle(self, *args, **options):
        cameras = Camera.objects.select_related("nvr")
        if options["cameras"]:
            cameras = cameras.filter(pk__in=options["cameras"])
        cameras = [camera for camera in cameras if camera.get_rtsp_url()]

        if not cameras:
            raise CommandError("No cameras with an RTSP URL to watch.")

        stop_event = threading.Event()
        threads = [
            threading.Thread(
                target=self._watch,
                args=(camera, options, stop_event),
                name=f"motion-{camera.id}",
                daemon=True,
            )
            for camera in cameras
        ]

        names = ", ".join(camera.name for camera in cameras)
        self.stdout.write(f"Watching {len(cameras)} camera(s): {names}")
        self.stdout.write("Press Ctrl-C to stop.")

        for thread in threads:
            thread.start()

        try:
            while any(thread.is_alive() for thread in threads):
                for thread in threads:
                    thread.join(timeout=0.5)
        except KeyboardInterrupt:
            self.stdout.write("\nStopping...")
            stop_event.set()
            for thread in threads:
                thread.join(timeout=10)

        self.stdout.write(self.style.SUCCESS("Watcher stopped."))

    def _watch(self, camera, options, stop_event):
        from devices.events import analyze_event

        try:
            events = motion.iter_motion_events(
                camera,
                min_area_ratio=options["min_area"],
                cooldown=options["cooldown"],
                sample_fps=options["sample_fps"],
                stop_event=stop_event,
            )
            for score, jpeg in events:
                event = record_motion_event(
                    camera,
                    MotionEvent.Source.FRAME_DIFF,
                    image_bytes=jpeg,
                    motion_score=score,
                )
                if event is None:
                    continue

                if options["analyze"]:
                    analyze_event(event)
                    self.stdout.write(
                        f"[{camera.name}] motion {score:.4f} -> event {event.pk}: "
                        f"{event.get_quality_display()} ({event.face_count} face(s))"
                    )
                else:
                    self.stdout.write(
                        f"[{camera.name}] motion {score:.4f} -> event {event.pk} (pending)"
                    )
        except Exception as exc:  # keep one bad camera from killing the process
            self.stderr.write(self.style.ERROR(f"[{camera.name}] watcher died: {exc}"))
