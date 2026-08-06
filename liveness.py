"""
Liveness / anti-spoofing for FaceMark.

Implements three cheap, real-time checks that together stop a casual
photo/video/mask attack on the kiosk:

1. Eye-blink challenge (EAR — eye aspect ratio over time).
   Real users blink every 2-6 s; a printed photo never blinks.

2. Texture-vs-screen score (Laplacian variance + frequency-domain energy).
   Phone / monitor / glossy print attacks show abnormally low high-frequency
   energy or moire — flagged as "screen".

3. Slow head-motion challenge — bounding-box drift over short window.
   A static photo held in front of the camera has near-zero motion.

The module exposes a stateful `LivenessTracker` keyed by face-id (bbox
coordinate hash). The recogniser asks `tracker.is_live(face_id, gray_face)`
before marking attendance. State is kept per-process; no DB writes.

If liveness is disabled in settings, `is_live` always returns True.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Optional

import cv2
import numpy as np

# ---------------------------------------------------------------------------
EAR_OPEN = 0.27           # eyes considered open above this ratio
EAR_CLOSE = 0.20          # below = closed (blink mid-point)
BLINK_WINDOW_SEC = 6.0    # need at least one blink in this window
MOTION_WINDOW_SEC = 4.0   # need this much bbox change in the window
MOTION_MIN_PX = 6         # minimum drift considered "real motion"
SCREEN_BLUR_FLOOR = 35.0  # blur below this + low high-freq => likely a screen

_eye_cascade_path = os.path.join(cv2.data.haarcascades, 'haarcascade_eye.xml')
_eye_cascade = cv2.CascadeClassifier(_eye_cascade_path) if os.path.exists(_eye_cascade_path) else None


def _eye_aspect_ratio(eye_bbox) -> float:
    """Cascade only gives a bbox, not landmarks, so we proxy EAR with the
    bbox aspect ratio (h/w). Closed eyes have a much smaller h/w than open."""
    _x, _y, w, h = eye_bbox
    if w <= 0:
        return 0.0
    return h / float(w)


def texture_score(face_bgr) -> dict:
    """Cheap screen-attack heuristic.

    Returns a dict with `blur`, `hf_energy` and `is_screen_like`.
    Print/screen attacks have flat texture (low blur) and lose energy in
    the high-frequency band of the FFT.
    """
    if face_bgr is None or face_bgr.size == 0:
        return {'blur': 0.0, 'hf_energy': 0.0, 'is_screen_like': True}
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # high-frequency energy via FFT magnitude in the outer ring of the spectrum
    f = np.fft.fftshift(np.fft.fft2(gray.astype(np.float32)))
    mag = np.log1p(np.abs(f))
    h, w = mag.shape
    cy, cx = h // 2, w // 2
    r = min(cy, cx) // 2
    mask = np.ones_like(mag, dtype=bool)
    mask[cy - r:cy + r, cx - r:cx + r] = False  # exclude low frequencies
    hf_energy = float(mag[mask].mean()) if mask.any() else 0.0

    return {
        'blur': blur,
        'hf_energy': hf_energy,
        'is_screen_like': (blur < SCREEN_BLUR_FLOOR and hf_energy < 6.0),
    }


# ---------------------------------------------------------------------------
class LivenessTracker:
    """Per-face state machine.

    Keys are stable face-IDs (the recogniser passes the predicted label, or
    a position-bucket hash for unknown faces). State is auto-pruned after
    inactivity to bound memory.
    """

    def __init__(self):
        self._eye_state: dict[str, str] = {}                # 'open' / 'closed'
        self._blinks: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=16))
        self._bboxes: dict[str, deque[tuple[float, tuple[int, int, int, int]]]] = defaultdict(lambda: deque(maxlen=64))
        self._last_seen: dict[str, float] = {}
        self._texture_strikes: dict[str, int] = defaultdict(int)

    # ---- public API ------------------------------------------------------
    def update(self, face_id: str, bbox, face_bgr) -> dict:
        """Push a new sighting; return current liveness verdict for `face_id`."""
        now = time.time()
        self._last_seen[face_id] = now
        self._bboxes[face_id].append((now, tuple(int(v) for v in bbox)))
        self._prune(now)

        # ---- 1) Blink via eye cascade
        blinked = False
        if _eye_cascade is not None:
            ear = self._detect_eye_ratio(face_bgr)
            prev = self._eye_state.get(face_id, 'open')
            if ear is None:
                pass
            elif ear < EAR_CLOSE and prev == 'open':
                self._eye_state[face_id] = 'closed'
            elif ear > EAR_OPEN and prev == 'closed':
                self._eye_state[face_id] = 'open'
                self._blinks[face_id].append(now)
                blinked = True

        # ---- 2) Texture / screen-attack
        tex = texture_score(face_bgr)
        if tex['is_screen_like']:
            self._texture_strikes[face_id] = min(8, self._texture_strikes[face_id] + 1)
        else:
            self._texture_strikes[face_id] = max(0, self._texture_strikes[face_id] - 1)

        # ---- 3) Motion: bbox variance over the window
        cutoff = now - MOTION_WINDOW_SEC
        recent = [b for ts, b in self._bboxes[face_id] if ts >= cutoff]
        motion_px = 0
        if len(recent) >= 2:
            xs = [r[0] + r[2] // 2 for r in recent]
            ys = [r[1] + r[3] // 2 for r in recent]
            motion_px = int(max(xs) - min(xs) + max(ys) - min(ys))

        blink_ok = self._has_recent_blink(face_id, now)
        motion_ok = motion_px >= MOTION_MIN_PX
        texture_ok = self._texture_strikes[face_id] < 4

        return {
            'live': blink_ok and motion_ok and texture_ok,
            'blink_ok': blink_ok,
            'motion_ok': motion_ok,
            'texture_ok': texture_ok,
            'just_blinked': blinked,
            'motion_px': motion_px,
            'blur': round(tex['blur'], 1),
        }

    def is_live(self, face_id: str, bbox, face_bgr) -> bool:
        return self.update(face_id, bbox, face_bgr)['live']

    def reset(self, face_id: Optional[str] = None) -> None:
        if face_id is None:
            self._eye_state.clear(); self._blinks.clear()
            self._bboxes.clear(); self._last_seen.clear()
            self._texture_strikes.clear()
        else:
            for d in (self._eye_state, self._blinks, self._bboxes,
                      self._last_seen, self._texture_strikes):
                d.pop(face_id, None)

    # ---- internals -------------------------------------------------------
    def _has_recent_blink(self, face_id: str, now: float) -> bool:
        dq = self._blinks.get(face_id)
        if not dq:
            return False
        return any(t >= now - BLINK_WINDOW_SEC for t in dq)

    def _detect_eye_ratio(self, face_bgr) -> Optional[float]:
        if face_bgr is None or face_bgr.size == 0 or _eye_cascade is None:
            return None
        gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
        h = gray.shape[0]
        upper = gray[: int(h * 0.65), :]
        eyes = _eye_cascade.detectMultiScale(upper, 1.1, 4, minSize=(18, 18))
        if len(eyes) == 0:
            # No eyes found could be a blink frame — caller treats as closed-ish
            return EAR_CLOSE - 0.01
        # Average eye aspect ratio across the two largest eyes
        eyes = sorted(eyes, key=lambda e: e[2] * e[3], reverse=True)[:2]
        ratios = [_eye_aspect_ratio(e) for e in eyes]
        return float(np.mean(ratios)) if ratios else None

    def _prune(self, now: float, ttl: float = 30.0) -> None:
        stale = [k for k, t in self._last_seen.items() if now - t > ttl]
        for k in stale:
            self.reset(k)


# Module-level singleton
_tracker = LivenessTracker()


def tracker() -> LivenessTracker:
    return _tracker


def make_face_id(label: Optional[str], bbox) -> str:
    """Stable per-frame identity key.
    Known faces use the predicted label; unknowns use a bucketed position
    so a moving photo doesn't get a different ID every frame."""
    if label:
        return label
    x, y, w, h = bbox
    return f'u_{x // 40}_{y // 40}_{w // 40}'
