"""
Face recogniser abstraction.

Prefers OpenCV LBPH (cv2.face.LBPHFaceRecognizer) — far more robust to
lighting / pose changes than raw-pixel KNN — and falls back to KNN
on raw pixels if cv2.face is not available.

Both backends expose the same interface:
    train(faces_dir)  -> bool
    predict(gray_face) -> (label_str | None, confidence_float)

For LBPH, "confidence" is the raw LBPH distance — lower is better.
For KNN we expose a normalised score in the same direction.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Optional, Tuple

import cv2
import numpy as np

import face_utils

log = logging.getLogger(__name__)

MODEL_DIR = 'static'
LBPH_MODEL = os.path.join(MODEL_DIR, 'lbph_model.yml')
LBPH_LABELS = os.path.join(MODEL_DIR, 'lbph_labels.json')
KNN_MODEL = os.path.join(MODEL_DIR, 'knn_model.pkl')

# Backend probe. cv2.face can exist as an empty namespace package when plain
# opencv-python shadows opencv-contrib-python, so probe the factory itself.
HAS_LBPH = hasattr(getattr(cv2, 'face', None), 'LBPHFaceRecognizer_create')


# ---------------------------------------------------------------------------
class _BaseRecognizer:
    name = 'base'

    def train(self, faces_dir: str) -> bool:
        raise NotImplementedError

    def predict(self, gray_face) -> Tuple[Optional[str], float]:
        raise NotImplementedError

    def is_trained(self) -> bool:
        raise NotImplementedError


# ---------------------------------------------------------------------------
class LBPHRecognizer(_BaseRecognizer):
    name = 'lbph'
    # LBPH confidence < this is accepted, larger = unknown
    default_threshold = 80.0

    def __init__(self):
        self._model = cv2.face.LBPHFaceRecognizer_create(
            radius=1, neighbors=8, grid_x=8, grid_y=8
        )
        self._labels: list[str] = []
        if self.is_trained():
            self._model.read(LBPH_MODEL)
            with open(LBPH_LABELS) as f:
                self._labels = json.load(f)

    def is_trained(self) -> bool:
        return os.path.exists(LBPH_MODEL) and os.path.exists(LBPH_LABELS)

    def train(self, faces_dir: str) -> bool:
        samples: list[np.ndarray] = []
        labels_int: list[int] = []
        label_names: list[str] = []

        for idx, user in enumerate(sorted(os.listdir(faces_dir))):
            user_path = os.path.join(faces_dir, user)
            if not os.path.isdir(user_path):
                continue
            label_names.append(user)
            for fname in os.listdir(user_path):
                img = cv2.imread(os.path.join(user_path, fname))
                if img is None:
                    continue
                gray = face_utils.preprocess(img)
                for variant in face_utils.augment(gray):
                    samples.append(variant)
                    labels_int.append(idx)

        if not samples:
            for p in (LBPH_MODEL, LBPH_LABELS):
                if os.path.exists(p):
                    os.remove(p)
            return False

        os.makedirs(MODEL_DIR, exist_ok=True)
        self._model = cv2.face.LBPHFaceRecognizer_create(
            radius=1, neighbors=8, grid_x=8, grid_y=8
        )
        self._model.train(samples, np.array(labels_int))
        self._model.write(LBPH_MODEL)
        with open(LBPH_LABELS, 'w') as f:
            json.dump(label_names, f)
        self._labels = label_names
        log.info('LBPH trained: %d samples, %d users', len(samples), len(label_names))
        return True

    def predict(self, gray_face) -> Tuple[Optional[str], float]:
        if not self._labels:
            return None, 9999.0
        try:
            label_int, confidence = self._model.predict(gray_face)
        except cv2.error:
            return None, 9999.0
        if 0 <= label_int < len(self._labels):
            return self._labels[label_int], float(confidence)
        return None, float(confidence)


# ---------------------------------------------------------------------------
class KNNFallbackRecognizer(_BaseRecognizer):
    """Fallback when opencv-contrib is unavailable. Raw-pixel KNN."""
    name = 'knn'
    default_threshold = 7000.0

    def __init__(self):
        self._model = None
        if self.is_trained():
            try:
                import joblib
                self._model = joblib.load(KNN_MODEL)
            except Exception as e:  # noqa: BLE001
                log.warning('failed to load KNN: %s', e)

    def is_trained(self) -> bool:
        return os.path.exists(KNN_MODEL)

    def train(self, faces_dir: str) -> bool:
        import joblib
        from sklearn.neighbors import KNeighborsClassifier
        X, y = [], []
        for user in sorted(os.listdir(faces_dir)):
            up = os.path.join(faces_dir, user)
            if not os.path.isdir(up):
                continue
            for fname in os.listdir(up):
                img = cv2.imread(os.path.join(up, fname))
                if img is None:
                    continue
                gray = face_utils.preprocess(img)
                for v in face_utils.augment(gray):
                    X.append(v.ravel()); y.append(user)
        if not X:
            if os.path.exists(KNN_MODEL):
                os.remove(KNN_MODEL)
            return False
        k = max(1, min(5, len(X)))
        m = KNeighborsClassifier(n_neighbors=k, weights='distance', metric='euclidean')
        m.fit(np.array(X), y)
        os.makedirs(MODEL_DIR, exist_ok=True)
        joblib.dump(m, KNN_MODEL)
        self._model = m
        return True

    def predict(self, gray_face) -> Tuple[Optional[str], float]:
        if self._model is None:
            return None, 9999.0
        dists, _ = self._model.kneighbors(gray_face.reshape(1, -1), n_neighbors=1)
        d = float(dists[0][0])
        return self._model.predict(gray_face.reshape(1, -1))[0], d


# ---------------------------------------------------------------------------
def _pick_backend():
    """Choose the best backend.

    Priority (configurable via `recognizer_backend` setting, default 'auto'):
       1. embeddings  - deep embeddings via ONNX or PCA+LDA
       2. lbph        - OpenCV-contrib LBPH
       3. knn         - raw-pixel KNN fallback
    """
    try:
        import db  # local to avoid import cycle at module load
        sel = (db.get_setting('recognizer_backend') or 'auto').lower()
    except Exception:  # noqa: BLE001
        sel = 'auto'

    if sel == 'embeddings':
        import embeddings
        return embeddings.get()
    if sel == 'lbph':
        return LBPHRecognizer() if HAS_LBPH else KNNFallbackRecognizer()
    if sel == 'knn':
        return KNNFallbackRecognizer()
    # auto
    return LBPHRecognizer() if HAS_LBPH else KNNFallbackRecognizer()


def get() -> _BaseRecognizer:
    """Return a singleton recogniser of the best available backend."""
    return _instance


_instance: _BaseRecognizer = LBPHRecognizer() if HAS_LBPH else KNNFallbackRecognizer()
log.info('recogniser backend: %s', _instance.name)


def retrain(faces_dir: str) -> bool:
    global _instance
    _instance = _pick_backend()
    if hasattr(_instance, 'train'):
        return _instance.train(faces_dir)
    return False
