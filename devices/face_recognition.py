import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# A face smaller than this on its shortest side carries too few pixels for SFace
# to embed reliably. 80px is a common floor for recognition (as opposed to mere
# detection, which works far smaller).
MIN_FACE_PX = 80
# Laplacian variance of the face crop below which the face is motion-blurred or
# out of focus. Heuristic — calibrate against your own captures.
MIN_FACE_SHARPNESS = 40.0
# DeepFace returns the whole frame as a pseudo-face when enforce_detection is
# off and nothing is found; anything at or below this confidence is discarded.
MIN_DETECTION_CONFIDENCE = 0.01


def _reference_db_path():
    from django.conf import settings
    return Path(settings.MEDIA_ROOT) / "persons"


def _ensure_db_exists():
    db = _reference_db_path()
    db.mkdir(parents=True, exist_ok=True)
    return db


def _load_image(path_or_bytes):
    import cv2
    import numpy as np

    if isinstance(path_or_bytes, (bytes, bytearray)):
        arr = np.frombuffer(path_or_bytes, dtype=np.uint8)
    else:
        with open(path_or_bytes, "rb") as f:
            arr = np.frombuffer(f.read(), dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return image


def recognize_people(image_path):
    """Return a list of recognized faces from an image file.

    Each result is a dict: {
        "name": str or "Unknown",
        "distance": float,
        "box": (x, y, w, h),
    }
    """
    import pandas as pd
    from deepface import DeepFace

    db_path = _ensure_db_exists()
    if not any(db_path.iterdir()):
        logger.info("No reference persons in %s; skipping recognition", db_path)
        return []

    try:
        results = DeepFace.find(
            img_path=image_path,
            db_path=str(db_path),
            model_name="SFace",
            detector_backend="opencv",
            distance_metric="cosine",
            enforce_detection=False,
            silent=True,
        )
    except Exception as exc:
        logger.warning("DeepFace recognition failed: %s", exc)
        return []

    # DeepFace.find can return a list of DataFrames (one per face) or a single DataFrame.
    if isinstance(results, pd.DataFrame):
        results = [results]

    faces = []
    for df in results:
        if df is None or df.empty:
            continue
        row = df.iloc[0]
        identity = row.get("identity", "")
        name = Path(identity).parent.name if identity else "Unknown"
        distance = float(row.get("distance", 1.0))
        threshold = float(row.get("threshold", 0.6))
        if distance > threshold:
            name = "Unknown"

        box = (
            int(row.get("source_x", 0)),
            int(row.get("source_y", 0)),
            int(row.get("source_w", 0)),
            int(row.get("source_h", 0)),
        )
        faces.append({"name": name, "distance": distance, "box": box})
    return faces


def detect_faces(image_path):
    """Detect faces without needing any enrolled Person references.

    Recognition is useless as a quality signal before anyone is enrolled, so
    detection is kept separate: it answers "is there a face here at all, and is
    it big and sharp enough to recognise later?".

    Returns a list of dicts: {"box": (x, y, w, h), "confidence": float,
    "sharpness": float}.
    """
    from deepface import DeepFace

    image = _load_image(image_path)
    if image is None:
        return []

    try:
        detections = DeepFace.extract_faces(
            img_path=image_path,
            detector_backend="opencv",
            enforce_detection=False,
            align=False,
        )
    except Exception as exc:
        logger.warning("DeepFace face detection failed: %s", exc)
        return []

    frame_h, frame_w = image.shape[:2]
    faces = []
    for detection in detections:
        confidence = float(detection.get("confidence", 0) or 0)
        area = detection.get("facial_area") or {}
        x, y = int(area.get("x", 0)), int(area.get("y", 0))
        w, h = int(area.get("w", 0)), int(area.get("h", 0))

        if confidence <= MIN_DETECTION_CONFIDENCE or w <= 0 or h <= 0:
            continue
        # The whole-frame fallback box is not a face.
        if w >= frame_w and h >= frame_h:
            continue

        crop = image[max(y, 0):y + h, max(x, 0):x + w]
        faces.append(
            {
                "box": (x, y, w, h),
                "confidence": confidence,
                "sharpness": _sharpness(crop),
            }
        )

    return faces


def _sharpness(image):
    """Laplacian variance of an image region. Higher is sharper."""
    import cv2

    if image is None or image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def assess_capture(image_path):
    """Judge whether a captured frame could support face recognition.

    Returns {"face_count", "best_box", "best_sharpness", "quality", "faces"}
    where quality is one of: no_face, too_small, too_blurry, usable.
    """
    faces = detect_faces(image_path)
    if not faces:
        return {
            "face_count": 0,
            "best_box": None,
            "best_sharpness": None,
            "quality": "no_face",
            "faces": [],
        }

    # "Best" = largest, since face size is the dominant constraint on recognition.
    best = max(faces, key=lambda f: f["box"][2] * f["box"][3])
    _, _, w, h = best["box"]

    if min(w, h) < MIN_FACE_PX:
        quality = "too_small"
    elif best["sharpness"] < MIN_FACE_SHARPNESS:
        quality = "too_blurry"
    else:
        quality = "usable"

    return {
        "face_count": len(faces),
        "best_box": best["box"],
        "best_sharpness": best["sharpness"],
        "quality": quality,
        "faces": faces,
    }


def draw_recognized_faces(image_path, faces):
    """Draw boxes and labels on the image and return JPEG bytes."""
    import cv2

    image = _load_image(image_path)
    if image is None:
        return None

    for face in faces:
        x, y, w, h = face["box"]
        name = face.get("name")
        if not name:
            # Detected but never matched against references (BGR amber).
            label, color = "Face", (0, 191, 255)
        elif name == "Unknown":
            label, color = name, (0, 0, 255)
        else:
            label, color = name, (0, 255, 0)
        cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)
        cv2.putText(
            image,
            label,
            (x, max(y - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
        )

    ok, jpeg = cv2.imencode(".jpg", image)
    return jpeg.tobytes() if ok else None
