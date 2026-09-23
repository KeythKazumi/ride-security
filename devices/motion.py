"""Software motion detection over a camera's RTSP stream.

The camera itself almost certainly does this better in hardware (VIGI cameras
expose motion, tamper and line-crossing events over ONVIF and the NVR OpenAPI),
but frame differencing needs no device-side configuration, so it works against
any camera that gives us RTSP.

A background subtractor (MOG2) maintains a model of the static scene and returns
a foreground mask per frame. When the share of foreground pixels crosses
`min_area_ratio` an *episode* begins: the detector keeps sampling frames at
`capture_interval` for as long as motion continues, and closes the episode
once the scene has been quiet for `quiet_seconds`. The whole sequence is
yielded so the caller can pick the most useful frames — someone walking toward
the camera is far away and tiny in the first frame and only becomes
recognisable near the end.

Frames arrive through `LatestFrameStream`: a reader thread drains the socket at
stream rate and keeps only the newest frame, so the detector always scores the
current scene instead of a backlog of stale frames.
"""

import logging
import time
from dataclasses import dataclass, field

from django.utils import timezone

logger = logging.getLogger(__name__)

# Fraction of the frame that must change before we call it motion. 0.5% of a
# 1080p frame is roughly a person-sized object at mid range.
DEFAULT_MIN_AREA_RATIO = 0.005
# Seconds of stillness that end an episode. Someone pausing to look at a
# doorbell is still one episode; a car passing and a person arriving a minute
# later are two.
DEFAULT_QUIET_SECONDS = 3.0
# Hard cap so a flapping curtain cannot hold an episode open for an hour.
DEFAULT_MAX_EPISODE_SECONDS = 60.0
# Seconds between frames sampled during an episode. Walking pace covers about
# 1.4 m per second, so one frame per second gives a shot at every step.
DEFAULT_CAPTURE_INTERVAL = 1.0
# Seconds to wait after an episode ends before a new one may start.
DEFAULT_COOLDOWN = 2.0
# Seconds the subtractor learns the scene before its output is trusted.
DEFAULT_WARMUP_SECONDS = 5.0
# Frames per second at which the trigger is evaluated. Every received frame is
# fed to the subtractor regardless — this only paces the (cheap) mask scoring.
DEFAULT_SAMPLE_FPS = 5
# The mask is computed on a downscaled frame: cheap enough to score at stream
# rate, and a pixel ratio means the same thing at any resolution.
ANALYSIS_WIDTH = 640


@dataclass
class Frame:
    """One sampled frame from an episode."""

    jpeg: bytes
    captured_at: object  # aware datetime
    motion_score: float
    sharpness: float


@dataclass
class Episode:
    """A continuous run of motion on one camera."""

    started_at: object  # aware datetime
    frames: list = field(default_factory=list)

    @property
    def peak_score(self):
        return max((f.motion_score for f in self.frames), default=0.0)


def _background_subtractor():
    import cv2

    return cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=False)


def _downscale(frame, width=ANALYSIS_WIDTH):
    import cv2

    if frame.shape[1] <= width:
        return frame
    scale = width / frame.shape[1]
    return cv2.resize(
        frame, (width, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA
    )


def sharpness(frame):
    """Laplacian variance — a cheap focus/motion-blur estimate. Higher is sharper."""
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _foreground_ratio(mask):
    """Return the fraction of a MOG2 mask flagged as foreground."""
    import cv2
    import numpy as np

    # MOG2 marks shadows as 127 when detectShadows is on; threshold keeps only
    # solid foreground either way.
    _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.dilate(mask, kernel, iterations=2)
    return float(np.count_nonzero(mask)) / mask.size


def _encode(frame):
    import cv2

    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return jpeg.tobytes() if ok else None


def iter_motion_episodes(
    camera,
    min_area_ratio=DEFAULT_MIN_AREA_RATIO,
    quiet_seconds=DEFAULT_QUIET_SECONDS,
    max_episode_seconds=DEFAULT_MAX_EPISODE_SECONDS,
    capture_interval=DEFAULT_CAPTURE_INTERVAL,
    cooldown=DEFAULT_COOLDOWN,
    warmup_seconds=DEFAULT_WARMUP_SECONDS,
    sample_fps=DEFAULT_SAMPLE_FPS,
    stop_event=None,
):
    """Yield an `Episode` for each continuous run of motion on `camera`.

    Runs until the stream dies for good or `stop_event` is set. `stop_event` is
    any object with an `is_set()` method (threading.Event).
    """
    from .streaming import LatestFrameStream

    rtsp_url = camera.get_rtsp_url()
    if not rtsp_url:
        logger.warning("No RTSP URL for camera %s; cannot watch for motion", camera.id)
        return

    stream = LatestFrameStream(rtsp_url).start()
    subtractor = None
    generation = -1
    last_seq = 0
    warm_until = 0.0
    interval = 1.0 / sample_fps if sample_fps else 0.0
    last_check = 0.0
    last_score_log = 0.0

    episode = None
    episode_started = 0.0
    last_motion = 0.0
    last_capture = 0.0
    episode_ended = 0.0

    def close_episode():
        nonlocal episode, episode_ended
        finished, episode = episode, None
        episode_ended = time.monotonic()
        if finished and finished.frames:
            logger.info(
                "Episode on camera %s: %s frame(s) over %.0fs, peak score %.4f",
                camera.id,
                len(finished.frames),
                (finished.frames[-1].captured_at - finished.started_at).total_seconds(),
                finished.peak_score,
            )
            return finished
        return None

    try:
        while not (stop_event and stop_event.is_set()):
            got = stream.read(after_seq=last_seq, timeout=1.0)
            if got is None:
                if stream.dead.is_set():
                    logger.error("Stream for camera %s died; stopping watcher", camera.id)
                    break
                if episode and time.monotonic() - last_motion >= quiet_seconds:
                    finished = close_episode()
                    if finished:
                        yield finished
                continue
            last_seq, gen, frame = got

            if gen != generation:
                # A reconnect means the old scene model belongs to a dead stream.
                generation = gen
                subtractor = _background_subtractor()
                warm_until = time.monotonic() + warmup_seconds

            mask = subtractor.apply(_downscale(frame))

            now = time.monotonic()
            if now - last_check < interval:
                continue
            last_check = now

            score = _foreground_ratio(mask)
            if now - last_score_log >= 5.0:
                logger.debug("Camera %s motion score: %.4f", camera.id, score)
                last_score_log = now

            moving = score >= min_area_ratio and now >= warm_until

            if episode is None:
                if moving and now - episode_ended >= cooldown:
                    episode = Episode(started_at=timezone.now())
                    episode_started = last_motion = now
                    last_capture = 0.0
                else:
                    continue
            elif moving:
                last_motion = now

            if now - last_motion >= quiet_seconds or now - episode_started >= max_episode_seconds:
                finished = close_episode()
                if finished:
                    yield finished
                continue

            if now - last_capture >= capture_interval:
                jpeg = _encode(frame)
                if jpeg:
                    episode.frames.append(
                        Frame(
                            jpeg=jpeg,
                            captured_at=timezone.now(),
                            motion_score=score,
                            sharpness=sharpness(frame),
                        )
                    )
                    last_capture = now

        # Stopped mid-episode: hand over what was collected.
        if episode:
            finished = close_episode()
            if finished:
                yield finished
    finally:
        stream.stop()
