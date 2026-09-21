import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# Force RTSP over TCP. The RTSP handshake on port 554 succeeds over NAT, but the
# RTP media stream defaults to UDP, whose return packets are dropped by Docker's
# bridge network (notably on Docker Desktop for macOS). The result is a capture
# that reports isOpened() == True but blocks on read() until FFmpeg's 30s
# timeout. The timeout is in microseconds and keeps unreachable hosts failing
# fast instead of hanging.
FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;tcp|timeout;5000000"

MAX_RECONNECTS = 3


def _open_capture(rtsp_url):
    """Open an RTSP stream with OpenCV."""
    import cv2

    # OpenCV reads this env var when the capture is constructed.
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", FFMPEG_CAPTURE_OPTIONS)

    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class LatestFrameStream:
    """Drain an RTSP stream on a dedicated thread, keeping only the newest frame.

    The stream runs over TCP, which never drops packets: a consumer that reads
    slower than the camera sends falls further behind every second, because
    cap.read() returns queued frames in order rather than the current one.
    Socket buffers then fill until the stream stalls or resets. Reading
    continuously and handing out only the latest frame keeps consumers looking
    at *now*; the ones in between are skipped, which is what a live consumer
    wants anyway.
    """

    def __init__(self, rtsp_url, max_reconnects=MAX_RECONNECTS):
        self._url = rtsp_url
        self._max_reconnects = max_reconnects
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0          # bumps with every frame received
        self._generation = 0   # bumps with every (re)connection
        self.dead = threading.Event()  # set when the stream gives up for good
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=5)

    def read(self, after_seq=0, timeout=None):
        """Return (seq, generation, frame) for a frame newer than `after_seq`.

        Blocks until a new frame arrives, `timeout` seconds pass, or the
        stream dies/stops — returning None in the latter cases. `generation`
        changes on every reconnect so consumers can drop state that was built
        from the previous stream (e.g. a background model).
        """
        with self._cond:
            self._cond.wait_for(
                lambda: self._seq > after_seq or self.dead.is_set() or self._stop.is_set(),
                timeout=timeout,
            )
            if self._seq <= after_seq:
                return None
            return self._seq, self._generation, self._frame

    def _run(self):
        reconnects = 0
        while not self._stop.is_set():
            cap = _open_capture(self._url)
            if not cap.isOpened():
                cap.release()
                reconnects += 1
                if reconnects >= self._max_reconnects:
                    logger.error("Cannot open RTSP stream after %s attempts", reconnects)
                    break
                time.sleep(1.0)
                continue

            reconnects = 0
            with self._cond:
                self._generation += 1
                self._cond.notify_all()

            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                with self._cond:
                    self._frame = frame
                    self._seq += 1
                    self._cond.notify_all()
            cap.release()

            if self._stop.is_set():
                break
            reconnects += 1
            if reconnects >= self._max_reconnects:
                logger.error("Giving up on stream after %s reconnects", reconnects)
                break
            logger.warning(
                "Lost stream, reconnecting (%s/%s)", reconnects, self._max_reconnects
            )
            time.sleep(1.0)

        self.dead.set()
        with self._cond:
            self._cond.notify_all()


def generate_mjpeg_stream(camera, fps=20):
    """Yield an MJPEG multipart stream from a camera's RTSP URL."""
    import cv2

    rtsp_url = camera.get_rtsp_url()
    if not rtsp_url:
        logger.warning("No RTSP URL for camera %s", camera.id)
        return

    stream = LatestFrameStream(rtsp_url).start()
    last_seq = 0
    interval = 1.0 / fps if fps else 0.0
    try:
        while True:
            got = stream.read(after_seq=last_seq, timeout=10.0)
            if got is None:
                if stream.dead.is_set():
                    logger.error("Stream for camera %s ended", camera.id)
                    break
                continue
            last_seq, _, frame = got

            ok, jpeg = cv2.imencode(".jpg", frame)
            if not ok:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
            )
            if interval:
                time.sleep(interval)
    finally:
        stream.stop()


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
