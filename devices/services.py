import logging

logger = logging.getLogger(__name__)


def fetch_camera_snapshot(camera):
    """Fetch a JPEG snapshot from a TP-Link NVR / camera.

    Supports a camera-level override `snapshot_url` or falls back to a
    TP-Link VIGI style HTTP snapshot endpoint built from the camera's NVR
    credentials. Returns bytes on success, None on failure.
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover
        logger.warning("requests is not installed; cannot fetch snapshot: %s", exc)
        return None

    url = camera.get_snapshot_url()
    if not url:
        logger.warning("No snapshot URL available for camera %s", camera)
        return None

    nvr = camera.nvr
    auth = None
    if nvr and nvr.username and nvr.password:
        # Many TP-Link devices expect credentials in the query string, but
        # some also accept HTTP Basic/Digest auth. Try both by passing auth
        # while the URL above already includes user/pwd query params.
        auth = (nvr.username, nvr.password)

    try:
        response = requests.get(url, auth=auth, timeout=10, stream=True)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" in content_type or response.content[:2] in (b"\xff\xd8", b"\x89\x50"):
            return response.content
        logger.warning("Camera %s snapshot returned non-image content-type: %s", camera, content_type)
        return None
    except requests.RequestException as exc:
        logger.warning("Failed to fetch camera %s snapshot: %s", camera, exc)
        return None
