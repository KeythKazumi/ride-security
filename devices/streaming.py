import logging
import time

logger = logging.getLogger(__name__)


def _open_capture(rtsp_url):
    """Open an RTSP stream with OpenCV."""
    import cv2

    cap = cv2.VideoCapture(rtsp_url)
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

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                # Try to reconnect once.
                cap.release()
                cap = _open_capture(rtsp_url)
                if not cap.isOpened():
                    break
                continue

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
