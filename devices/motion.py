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
# Frames fed to the subtractor before it is trusted; it needs to learn the scene.
DEFAULT_WARMUP_FRAMES = 30
# Frames per second actually analysed. Motion detection does not need 20fps and
# this keeps CPU sane when watching several cameras.
DEFAULT_SAMPLE_FPS = 5
# Frames collected after a trigger, from which the sharpest is kept.
DEFAULT_BURST_FRAMES = 5

MAX_RECONNECTS = 3


def _background_subtractor():
    import cv2

    return cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=25, detectShadows=False)


def sharpness(frame):
    """Laplacian variance — a cheap focus/motion-blur estimate. Higher is sharper."""
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _foreground_ratio(subtractor, frame):
    """Return the fraction of the frame flagged as foreground."""
    import cv2
    import numpy as np

    mask = subtractor.apply(frame)
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
    warmup_frames=DEFAULT_WARMUP_FRAMES,
    sample_fps=DEFAULT_SAMPLE_FPS,
    burst_frames=DEFAULT_BURST_FRAMES,
    stop_event=None,
):
    """Yield `(motion_score, jpeg_bytes)` for each motion event on `camera`.

    Runs until the stream dies for good or `stop_event` is set. `stop_event` is
    any object with an `is_set()` method (threading.Event).
    """
    import cv2

    from .streaming import _open_capture

    rtsp_url = camera.get_rtsp_url()
    if not rtsp_url:
        logger.warning("No RTSP URL for camera %s; cannot watch for motion", camera.id)
        return

    cap = _open_capture(rtsp_url)
    if not cap.isOpened():
        logger.error("Cannot open RTSP stream for camera %s", camera.id)
        return

    subtractor = _background_subtractor()
    frames_seen = 0
    reconnects = 0
    armed_at = 0.0
    interval = 1.0 / sample_fps if sample_fps else 0.0

    try:
        while not (stop_event and stop_event.is_set()):
            ok, frame = cap.read()
            if not ok:
                if reconnects >= MAX_RECONNECTS:
                    logger.error(
                        "Giving up on camera %s after %s reconnect attempts", camera.id, reconnects
                    )
                    break
                reconnects += 1
                logger.warning(
                    "Lost stream for camera %s, reconnecting (%s/%s)",
                    camera.id,
                    reconnects,
                    MAX_RECONNECTS,
                )
                cap.release()
                cap = _open_capture(rtsp_url)
                if not cap.isOpened():
                    break
                # The scene model is stale after a reconnect.
                subtractor = _background_subtractor()
                frames_seen = 0
                continue

            reconnects = 0
            frames_seen += 1
            score = _foreground_ratio(subtractor, frame)

            in_warmup = frames_seen <= warmup_frames
            in_cooldown = time.monotonic() - armed_at < cooldown
            if score >= min_area_ratio and not in_warmup and not in_cooldown:
                best = frame
                best_sharpness = sharpness(frame)
                for _ in range(max(burst_frames - 1, 0)):
                    ok, candidate = cap.read()
                    if not ok:
                        break
                    subtractor.apply(candidate)
                    candidate_sharpness = sharpness(candidate)
                    if candidate_sharpness > best_sharpness:
                        best, best_sharpness = candidate, candidate_sharpness

                encoded, jpeg = cv2.imencode(".jpg", best)
                if encoded:
                    armed_at = time.monotonic()
                    logger.info(
                        "Motion on camera %s: score=%.4f sharpness=%.1f", camera.id, score, best_sharpness
                    )
                    yield score, jpeg.tobytes()
                else:
                    logger.warning("Could not encode motion frame for camera %s", camera.id)

            if interval:
                time.sleep(interval)
    finally:
        cap.release()
