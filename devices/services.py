import logging

logger = logging.getLogger(__name__)


def fetch_camera_snapshot(camera):
    """Fetch a JPEG snapshot from a TP-Link NVR / camera.

    Supports a camera-level override `snapshot_url` or falls back to a
    TP-Link VIGI style HTTP snapshot endpoint built from the camera's NVR
    credentials. If the HTTP snapshot fails, falls back to extracting a frame
    from the RTSP stream. Returns bytes on success, None on failure.
    """
    from .streaming import get_rtsp_frame

    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        logger.warning("requests is not installed; cannot fetch snapshot: %s", exc)
        return get_rtsp_frame(camera)

    url = camera.get_snapshot_url()
    if url:
        nvr = camera.nvr
        auth = None
        if nvr and nvr.username and nvr.password:
            auth = (nvr.username, nvr.password)

        try:
            response = requests.get(url, auth=auth, timeout=10, stream=True)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "image" in content_type or response.content[:2] in (b"\xff\xd8", b"\x89\x50"):
                return response.content
            logger.warning("Camera %s snapshot returned non-image content-type: %s", camera, content_type)
        except requests.RequestException as exc:
            logger.warning("Failed to fetch camera %s HTTP snapshot, trying RTSP: %s", camera, exc)

    return get_rtsp_frame(camera)
