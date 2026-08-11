"""
Face preprocessing utilities.

- detect_faces       — Haar cascade + histogram equalisation
- align_face         — rotate so the eyes are horizontal (big accuracy gain)
- preprocess         — full pipeline: align → gray → resize → equalise
- augment            — produce small batch of variants for enrollment
- quality_score      — sharpness via Laplacian variance (blur rejection)
"""

from __future__ import annotations

import os
from typing import Iterable

import cv2
import numpy as np

FACE_SIZE = (200, 200)            # LBPH likes larger, square, gray crops
MIN_FACE_PX = 50                  # crowd-friendly: faces at the back are small
BLUR_THRESHOLD = 60.0             # Laplacian variance below this = too blurry

_HAAR_FACE = 'haarcascade_frontalface_default.xml'
_HAAR_EYE = 'haarcascade_eye.xml'

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_cascade(filename: str) -> str | None:
    """Locate a Haar XML by absolute path.

    Order: OpenCV's bundled data dir (always present with opencv-python), then
    a copy sitting next to this module, then the process CWD.

    The face cascade used to be loaded as the bare relative name
    'haarcascade_frontalface_default.xml'. That only resolves when the process
    happens to be started from the repo root — a systemd unit without
    WorkingDirectory=, a `python /path/to/app.py` from elsewhere, or any
    supervisor with a different CWD gets an *empty* classifier. OpenCV does not
    raise on the failed load; it raises later, on every single frame, with
    "(-215:Assertion failed) !empty() in function 'detectMultiScale'", which
    reads like a corrupt-image bug rather than a missing-file one.
    """
    for candidate in (os.path.join(cv2.data.haarcascades, filename),
                      os.path.join(_HERE, filename),
                      os.path.abspath(filename)):
        if os.path.exists(candidate):
            return candidate
    return None


_face_cascade_path = _find_cascade(_HAAR_FACE)
if _face_cascade_path is None:
    raise RuntimeError(
        f'Cannot find {_HAAR_FACE}. Looked in cv2.data.haarcascades '
        f'({cv2.data.haarcascades}), {_HERE}, and the CWD. Reinstall '
        'opencv-contrib-python or restore the XML to the project root.'
    )
_face_cascade = cv2.CascadeClassifier(_face_cascade_path)
if _face_cascade.empty():
    raise RuntimeError(f'{_face_cascade_path} exists but failed to parse.')

_eye_cascade_path = _find_cascade(_HAAR_EYE)
_eye_cascade = cv2.CascadeClassifier(_eye_cascade_path) if _eye_cascade_path else None
if _eye_cascade is not None and _eye_cascade.empty():
    _eye_cascade = None      # align_face() degrades to a no-op


# ---------------------------------------------------------------------------
def detect_faces(frame_bgr):
    if frame_bgr is None or frame_bgr.size == 0:
        return []
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    return _face_cascade.detectMultiScale(
        gray, scaleFactor=1.15, minNeighbors=6, minSize=(MIN_FACE_PX, MIN_FACE_PX)
    )


def quality_score(face_bgr) -> float:
    """Higher = sharper. Below BLUR_THRESHOLD treat as unusable."""
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def align_face(face_bgr):
    """Detect both eyes inside the face crop and rotate so they're horizontal.
    If eyes can't be found, return the crop unchanged."""
    if _eye_cascade is None:
        return face_bgr
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    # only look in the upper 65% — eyes aren't in the chin
    h = gray.shape[0]
    upper = gray[: int(h * 0.65), :]
    eyes = _eye_cascade.detectMultiScale(upper, 1.1, 4, minSize=(20, 20))
    if len(eyes) < 2:
        return face_bgr

    # pick two largest, treat them as left/right
    eyes = sorted(eyes, key=lambda e: e[2] * e[3], reverse=True)[:2]
    eyes = sorted(eyes, key=lambda e: e[0])
    (lx, ly, lw, lh), (rx, ry, rw, rh) = eyes
    lc = (lx + lw / 2.0, ly + lh / 2.0)
    rc = (rx + rw / 2.0, ry + rh / 2.0)
    dy, dx = rc[1] - lc[1], rc[0] - lc[0]
    angle = np.degrees(np.arctan2(dy, dx))
    if abs(angle) < 1.0:
        return face_bgr  # already level enough
    cx, cy = face_bgr.shape[1] / 2.0, face_bgr.shape[0] / 2.0
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    return cv2.warpAffine(face_bgr, M, (face_bgr.shape[1], face_bgr.shape[0]),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def preprocess(face_bgr):
    """Align, gray, resize to FACE_SIZE, equalise histogram. Returns uint8 2D."""
    aligned = align_face(face_bgr)
    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, FACE_SIZE, interpolation=cv2.INTER_AREA)
    return cv2.equalizeHist(resized)


# ---------------------------------------------------------------------------
def augment(gray_face) -> Iterable[np.ndarray]:
    """Yield the original plus mirrored + brightness-shifted versions.
    Roughly 5x the training data without overfitting."""
    yield gray_face
    # horizontal flip
    yield cv2.flip(gray_face, 1)
    # brighter / darker
    for delta in (-20, 20):
        shifted = np.clip(gray_face.astype(np.int16) + delta, 0, 255).astype(np.uint8)
        yield shifted
    # small rotations
    for angle in (-5, 5):
        h, w = gray_face.shape
        M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
        rot = cv2.warpAffine(gray_face, M, (w, h),
                             flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        yield rot
