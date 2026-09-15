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
# Detector fallback order: opencv's Haar cascade is fast but misses profiles and
# hard hats, so a miss falls through to MTCNN before giving up on the frame.
FACE_DETECTORS = ("opencv", "mtcnn")


PERSON_DB = "persons"
CANDIDATE_DB = "candidates"

# MobileNet-SSD person detector (VOC class 15). Model files live with the other
# cached weights and are fetched on first use, like DeepFace's own models.
SSD_PERSON_CLASS = 15
SSD_MIN_CONFIDENCE = 0.15
PERSON_MODEL_FILES = ("MobileNetSSD_deploy.prototxt", "MobileNetSSD_deploy.caffemodel")
PERSON_MODEL_URL = (
    "https://github.com/PINTO0309/MobileNet-SSD-RealSense/raw/master/"
    "caffemodel/MobileNetSSD/"
)
_PERSON_NET = None


def db_path(name=PERSON_DB, ensure=True):
    """Path of a DeepFace directory-backed face database under MEDIA_ROOT."""
    from django.conf import settings

    path = Path(settings.MEDIA_ROOT) / name
    if ensure:
        path.mkdir(parents=True, exist_ok=True)
    return path


def invalidate_db_cache(name=PERSON_DB):
    """Drop DeepFace's cached embeddings for a database.

    DeepFace pickles representations next to the images. It refreshes on added
    files, but renaming or merging directories moves identities around behind
    its back, so the cache has to go or stale identities keep matching.
    """
    path = db_path(name, ensure=False)
    if not path.exists():
        return
    for pickle_file in path.glob("*.pkl"):
        try:
            pickle_file.unlink()
        except OSError as exc:
            logger.warning("Could not remove %s: %s", pickle_file, exc)


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


def search_faces(image_path, db_name=PERSON_DB):
    """Match every face in an image against a face database.

    Returns a list of dicts: {"code", "matched", "distance", "threshold",
    "box"}. `code` is the identity directory name (never a display name — the
    caller resolves it through the ORM), and is None when nothing matched
    closely enough.
    """
    import pandas as pd
    from deepface import DeepFace

    path = db_path(db_name)
    if not any(path.iterdir()):
        logger.info("No references in %s; skipping search", path)
        return []

    image = _load_image(image_path)
    if image is None:
        return []
    frame_h, frame_w = image.shape[:2]

    for backend in FACE_DETECTORS:
        faces = _search_with(image_path, path, backend, frame_w, frame_h)
        if faces or backend == FACE_DETECTORS[-1]:
            return faces
    return []


def _search_with(image_path, path, backend, frame_w, frame_h):
    """One DeepFace.find pass with a given detector backend."""
    import pandas as pd
    from deepface import DeepFace

    try:
        results = DeepFace.find(
            img_path=image_path,
            db_path=str(path),
            model_name="SFace",
            detector_backend=backend,
            distance_metric="cosine",
            enforce_detection=False,
            silent=True,
        )
    except Exception as exc:
        logger.warning("DeepFace search in %s failed (%s): %s", path.name, backend, exc)
        return []

    # DeepFace.find can return a list of DataFrames (one per face) or a single DataFrame.
    if isinstance(results, pd.DataFrame):
        results = [results]

    faces = []
    for df in results:
        if df is None or df.empty:
            continue
        row = df.iloc[0]
        w, h = int(row.get("source_w", 0)), int(row.get("source_h", 0))
        # The whole-frame fallback box is not a face — and must never match.
        if w >= frame_w and h >= frame_h:
            continue
        identity = row.get("identity", "")
        distance = float(row.get("distance", 1.0))
        threshold = float(row.get("threshold", 0.6))
        matched = bool(identity) and distance <= threshold

        faces.append(
            {
                "code": Path(identity).parent.name if matched else None,
                "matched": matched,
                "distance": distance,
                "threshold": threshold,
                "box": (
                    int(row.get("source_x", 0)),
                    int(row.get("source_y", 0)),
                    w,
                    h,
                ),
            }
        )
    return faces


def detect_faces(image_path):
    """Detect faces without needing any enrolled Person references.

    Recognition is useless as a quality signal before anyone is enrolled, so
    detection is kept separate: it answers "is there a face here at all, and is
    it big and sharp enough to recognise later?".

    Returns a list of dicts: {"box": (x, y, w, h), "confidence": float,
    "sharpness": float}.
    """
    image = _load_image(image_path)
    if image is None:
        return []

    for backend in FACE_DETECTORS:
        faces = _detect_with(image, backend)
        if faces:
            return faces
    return []


def _detect_with(image, backend):
    """Run one detector backend over an already-decoded frame."""
    from deepface import DeepFace

    try:
        detections = DeepFace.extract_faces(
            img_path=image,
            detector_backend=backend,
            enforce_detection=False,
            align=False,
        )
    except Exception as exc:
        logger.warning("DeepFace face detection failed (%s): %s", backend, exc)
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


def _person_net():
    """Lazy-load the MobileNet-SSD person detector, fetching weights on demand."""
    global _PERSON_NET
    if _PERSON_NET is not None:
        return _PERSON_NET or None

    import cv2

    directory = Path.home() / ".deepface" / "weights"
    proto, model = (directory / f for f in PERSON_MODEL_FILES)
    if not (proto.exists() and model.exists()):
        try:
            import urllib.request

            directory.mkdir(parents=True, exist_ok=True)
            for filename in PERSON_MODEL_FILES:
                urllib.request.urlretrieve(PERSON_MODEL_URL + filename, directory / filename)
        except Exception as exc:
            logger.warning("Could not fetch person-detection model: %s", exc)
            _PERSON_NET = False
            return None

    try:
        _PERSON_NET = cv2.dnn.readNetFromCaffe(str(proto), str(model))
    except Exception as exc:
        logger.warning("Could not load person-detection model: %s", exc)
        _PERSON_NET = False
    return _PERSON_NET or None


def detect_persons(image_path):
    """Best-effort pedestrian detection for frames where no usable face exists.

    A person with their back turned or a face under the pixel floor still
    matters for review — the capture is evidence a human triggered it. Two
    weak-but-free detectors are unioned: MobileNet-SSD's person class at a
    608px input, and OpenCV's HOG people detector on a 1.5x upscale for
    distant figures. Overlapping hits are merged with NMS.

    Returns a list of (x, y, w, h) boxes.
    """
    import cv2
    import numpy as np

    image = _load_image(image_path)
    if image is None:
        return []

    frame_h, frame_w = image.shape[:2]
    boxes, scores = [], []

    net = _person_net()
    if net is not None:
        blob = cv2.dnn.blobFromImage(
            cv2.resize(image, (608, 608)), 0.007843, (608, 608), 127.5
        )
        net.setInput(blob)
        detections = net.forward()
        for i in range(detections.shape[2]):
            confidence = float(detections[0, 0, i, 2])
            if int(detections[0, 0, i, 1]) != SSD_PERSON_CLASS or confidence < SSD_MIN_CONFIDENCE:
                continue
            x1, y1, x2, y2 = (
                detections[0, 0, i, 3:7] * [frame_w, frame_h, frame_w, frame_h]
            ).astype(int)
            boxes.append(
                [max(int(x1), 0), max(int(y1), 0), min(int(x2 - x1), frame_w), min(int(y2 - y1), frame_h)]
            )
            scores.append(confidence)

    # HOG on a 1.5x upscale catches distant upright figures the SSD misses.
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    big = cv2.resize(image, None, fx=1.5, fy=1.5)
    rects, weights = hog.detectMultiScale(big, winStride=(8, 8), padding=(8, 8), scale=1.05)
    for rect, weight in zip(rects, weights):
        if weight <= 0.3:
            continue
        x, y, w, h = (int(v / 1.5) for v in rect)
        boxes.append([x, y, w, h])
        scores.append(float(min(weight, 1.0)))

    if not boxes:
        return []
    keep = np.array(
        cv2.dnn.NMSBoxes(boxes, scores, score_threshold=0.0, nms_threshold=0.4)
    ).ravel()
    return [tuple(int(v) for v in boxes[i]) for i in keep]


def _sharpness(image):
    """Laplacian variance of an image region. Higher is sharper."""
    import cv2

    if image is None or image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def crop_face(image_path, box, margin=0.3):
    """Return a JPEG of just the face, with margin, for use as a reference.

    Enrolling the full frame would embed the background too and makes the
    thumbnails useless; the margin keeps enough of the head for the detector to
    find the face again inside the crop.
    """
    import cv2

    image = _load_image(image_path)
    if image is None:
        return None

    x, y, w, h = box
    pad_x, pad_y = int(w * margin), int(h * margin)
    frame_h, frame_w = image.shape[:2]
    x0, y0 = max(x - pad_x, 0), max(y - pad_y, 0)
    x1, y1 = min(x + w + pad_x, frame_w), min(y + h + pad_y, frame_h)

    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        return None

    ok, jpeg = cv2.imencode(".jpg", crop)
    return jpeg.tobytes() if ok else None


def face_quality(image_path, box):
    """Size and sharpness verdict for one face box."""
    image = _load_image(image_path)
    if image is None:
        return {"width": 0, "height": 0, "sharpness": 0.0, "usable": False}

    x, y, w, h = box
    crop = image[max(y, 0):y + h, max(x, 0):x + w]
    sharp = _sharpness(crop)
    return {
        "width": w,
        "height": h,
        "sharpness": sharp,
        "usable": min(w, h) >= MIN_FACE_PX and sharp >= MIN_FACE_SHARPNESS,
    }


def assess_capture(image_path):
    """Judge whether a captured frame could support face recognition.

    Returns {"face_count", "best_box", "best_sharpness", "quality", "faces",
    "persons"} where quality is one of: usable, too_small, too_blurry,
    person, no_face. `person` means a human shape was found but no face was
    usable — the capture still matters and must not be auto-purged.
    """
    faces = detect_faces(image_path)
    persons = detect_persons(image_path)
    if not faces:
        return {
            "face_count": 0,
            "best_box": None,
            "best_sharpness": None,
            "quality": "person" if persons else "no_face",
            "faces": [],
            "persons": persons,
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
        "persons": persons,
    }


def draw_recognized_faces(image_path, faces, persons=None):
    """Draw boxes and labels on the image and return JPEG bytes."""
    import cv2

    image = _load_image(image_path)
    if image is None:
        return None

    for x, y, w, h in persons or []:
        # Detected person shape without a usable face (BGR magenta).
        cv2.rectangle(image, (x, y), (x + w, y + h), (255, 0, 255), 2)
        cv2.putText(
            image,
            "Person",
            (x, max(y - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 0, 255),
            2,
        )

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
