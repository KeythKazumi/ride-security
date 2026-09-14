import logging
import os
import time

logger = logging.getLogger(__name__)

# Force RTSP over TCP. The RTSP handshake on port 554 succeeds over NAT, but the
# RTP media stream defaults to UDP, whose return packets are dropped by Docker's
# bridge network (notably on Docker Desktop for macOS). The result is a capture
# that reports isOpened() == True but blocks on read() until FFmpeg's 30s
# timeout. The timeout is in microseconds and keeps unreachable hosts failing
# fast instead of hanging.
FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;tcp|timeout;5000000"


def _open_capture(rtsp_url):
    """Open an RTSP stream with OpenCV."""
    import cv2

    # OpenCV reads this env var when the capture is constructed.
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", FFMPEG_CAPTURE_OPTIONS)

    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def generate_mjpeg_stream(camera, fps=20):
    """Yield an MJPEG multipart stream from a camera's RTSP URL."""
    import cv2

    rtsp_url = camera.get_rtsp_url()
    if not rtsp_url:
        logger.warning("No RTSP URL for camera %s", camera.id)
        return

    cap = _open_capture(rtsp_url)
    if not cap.isOpened():
        logger.error("Cannot open RTSP stream for camera %s: %s", camera.id, rtsp_url)
        return

    max_reconnects = 3
    reconnects = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                if reconnects >= max_reconnects:
                    logger.error(
                        "Giving up on camera %s after %s reconnect attempts",
                        camera.id,
                        reconnects,
                    )
                    break
                reconnects += 1
                logger.warning(
                    "Lost stream for camera %s, reconnecting (%s/%s)",
                    camera.id,
                    reconnects,
                    max_reconnects,
                )
                cap.release()
                cap = _open_capture(rtsp_url)
                if not cap.isOpened():
                    break
                continue

            reconnects = 0

            ok, jpeg = cv2.imencode(".jpg", frame)
            if not ok:
                continue

            jpeg_bytes = jpeg.tobytes()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg_bytes + b"\r\n"
            )
            time.sleep(1.0 / fps)
    finally:
        cap.release()


def get_rtsp_frame(camera, timeout=10):
    """Capture a single JPEG frame from the camera's RTSP stream."""
    import cv2

    rtsp_url = camera.get_rtsp_url()
    if not rtsp_url:
        return None

    cap = _open_capture(rtsp_url)
    if not cap.isOpened():
        return None

    try:
        for _ in range(int(timeout * 10)):
            ok, frame = cap.read()
            if ok:
                ok, jpeg = cv2.imencode(".jpg", frame)
                if ok:
                    return jpeg.tobytes()
            time.sleep(0.1)
    finally:
        cap.release()

    return None
