"""Software motion detection over a camera's RTSP stream.

The camera itself almost certainly does this better in hardware (VIGI cameras
expose motion, tamper and line-crossing events over ONVIF and the NVR OpenAPI),
but frame differencing needs no device-side configuration, so it works against
any camera that gives us RTSP.

A background subtractor (MOG2) maintains a model of the static scene and returns
a foreground mask per frame. When the share of foreground pixels crosses
`min_area_ratio` the detector picks the sharpest frame from a short burst — a
blurry frame is useless for face recognition even when the motion is real — and
yields it as JPEG bytes.

Frames arrive through `LatestFrameStream`: a reader thread drains the socket at
stream rate and keeps only the newest frame, so the detector always scores the
current scene instead of a backlog of stale frames.
"""

import logging
import time

logger = logging.getLogger(__name__)

# Fraction of the frame that must change before we call it motion. 0.5% of a
# 1080p frame is roughly a person-sized object at mid range.
DEFAULT_MIN_AREA_RATIO = 0.005
# Seconds to wait after a capture before arming again, so one person walking
# past does not produce fifty rows.
DEFAULT_COOLDOWN = 15.0
# Seconds the subtractor learns the scene before its output is trusted.
DEFAULT_WARMUP_SECONDS = 5.0
# Frames per second at which the trigger is evaluated. Every received frame is
# fed to the subtractor regardless — this only paces the (cheap) mask scoring.
DEFAULT_SAMPLE_FPS = 5
# Frames collected after a trigger, from which the sharpest is kept.
DEFAULT_BURST_FRAMES = 5
# The mask is computed on a downscaled frame: cheap enough to score at stream
# rate, and a pixel ratio means the same thing at any resolution.
ANALYSIS_WIDTH = 640


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


def iter_motion_events(
    camera,
    min_area_ratio=DEFAULT_MIN_AREA_RATIO,
    cooldown=DEFAULT_COOLDOWN,
    warmup_seconds=DEFAULT_WARMUP_SECONDS,
    sample_fps=DEFAULT_SAMPLE_FPS,
    burst_frames=DEFAULT_BURST_FRAMES,
    stop_event=None,
):
    """Yield `(motion_score, jpeg_bytes)` for each motion event on `camera`.

    Runs until the stream dies for good or `stop_event` is set. `stop_event` is
    any object with an `is_set()` method (threading.Event).
    """
    import cv2

    from .streaming import LatestFrameStream

    rtsp_url = camera.get_rtsp_url()
    if not rtsp_url:
        logger.warning("No RTSP URL for camera %s; cannot watch for motion", camera.id)
        return

    stream = LatestFrameStream(rtsp_url).start()
    subtractor = None
    generation = -1
    last_seq = 0
    armed_at = 0.0
    warm_until = 0.0
    interval = 1.0 / sample_fps if sample_fps else 0.0
    last_check = 0.0
    last_score_log = 0.0

    try:
        while not (stop_event and stop_event.is_set()):
            got = stream.read(after_seq=last_seq, timeout=1.0)
            if got is None:
                if stream.dead.is_set():
                    logger.error("Stream for camera %s died; stopping watcher", camera.id)
                    break
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

            in_warmup = now < warm_until
            in_cooldown = now - armed_at < cooldown
            if score < min_area_ratio or in_warmup or in_cooldown:
                continue

            best = frame
            best_sharpness = sharpness(frame)
            for _ in range(max(burst_frames - 1, 0)):
                nxt = stream.read(after_seq=last_seq, timeout=1.0)
                if nxt is None:
                    break
                last_seq, burst_gen, candidate = nxt
                if burst_gen == generation:
                    subtractor.apply(_downscale(candidate))
                candidate_sharpness = sharpness(candidate)
                if candidate_sharpness > best_sharpness:
                    best, best_sharpness = candidate, candidate_sharpness

            encoded, jpeg = cv2.imencode(".jpg", best)
            if encoded:
                armed_at = time.monotonic()
                logger.info(
                    "Motion on camera %s: score=%.4f sharpness=%.1f",
                    camera.id,
                    score,
                    best_sharpness,
                )
                yield score, jpeg.tobytes()
            else:
                logger.warning("Could not encode motion frame for camera %s", camera.id)
    finally:
        stream.stop()
