"""Long-running motion watcher.

Each camera gets its own thread, because `iter_motion_episodes` blocks on the
stream. Threads (not processes) are fine here: the work is either waiting on
the network or inside OpenCV, which releases the GIL.
"""

import threading

from django.core.management.base import BaseCommand, CommandError

from devices import events, motion
from devices.models import Camera


class Command(BaseCommand):
    help = (
        "Watch camera RTSP streams for motion. Each continuous run of motion is "
        "sampled once a second and its best frames are stored as one sequence."
    )

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
            "--quiet",
            type=float,
            default=motion.DEFAULT_QUIET_SECONDS,
            help=f"Seconds of stillness that end an episode (default: {motion.DEFAULT_QUIET_SECONDS}).",
        )
        parser.add_argument(
            "--max-episode",
            type=float,
            default=motion.DEFAULT_MAX_EPISODE_SECONDS,
            help=f"Longest an episode may run, seconds (default: {motion.DEFAULT_MAX_EPISODE_SECONDS:g}).",
        )
        parser.add_argument(
            "--capture-interval",
            type=float,
            default=motion.DEFAULT_CAPTURE_INTERVAL,
            help=f"Seconds between sampled frames during an episode (default: {motion.DEFAULT_CAPTURE_INTERVAL}).",
        )
        parser.add_argument(
            "--keep-frames",
            type=int,
            default=events.DEFAULT_KEEP_FRAMES,
            help=f"Best frames stored per episode; 0 keeps every frame with a person (default: {events.DEFAULT_KEEP_FRAMES}).",
        )
        parser.add_argument(
            "--cooldown",
            type=float,
            default=motion.DEFAULT_COOLDOWN,
            help=f"Seconds between episodes per camera (default: {motion.DEFAULT_COOLDOWN}).",
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
        try:
            episodes = motion.iter_motion_episodes(
                camera,
                min_area_ratio=options["min_area"],
                quiet_seconds=options["quiet"],
                max_episode_seconds=options["max_episode"],
                capture_interval=options["capture_interval"],
                cooldown=options["cooldown"],
                sample_fps=options["sample_fps"],
                stop_event=stop_event,
            )
            for episode in episodes:
                saved = events.record_motion_episode(camera, episode, keep=options["keep_frames"])
                if not saved:
                    self.stdout.write(
                        f"[{camera.name}] episode of {len(episode.frames)} frame(s): no person, dropped"
                    )
                    continue

                best = saved[0]
                line = (
                    f"[{camera.name}] episode of {len(episode.frames)} frame(s) -> kept {len(saved)} "
                    f"as sequence {best.sequence[:8]}, best event {best.pk}"
                )
                if options["analyze"]:
                    for event in saved:
                        events.analyze_event(event)
                    line += f": {best.get_quality_display()} ({best.face_count} face(s))"
                else:
                    line += " (pending)"
                self.stdout.write(line)
        except Exception as exc:  # keep one bad camera from killing the process
            self.stderr.write(self.style.ERROR(f"[{camera.name}] watcher died: {exc}"))
