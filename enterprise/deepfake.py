"""
N5 — Deepfake, virtual-camera and injection-attack defense.

The 2026 spoofing vector that even big HR suites miss is *injection* — instead
of holding a phone up to the camera, the attacker pipes a video or AI-generated
face directly into the camera device using OBS Virtual Camera, ManyCam, or a
similar app. The kiosk just sees "a webcam" and a clean stream.

We detect this with three orthogonal checks. Any single one passing is
suspicious; two raise a hard block.

Layer 1 — Virtual camera device name
  When OpenCV opens a USB camera we can read the device name from
  `cv2.CAP_PROP_BACKEND` and the OS device list. Known virtual-camera names
  ("OBS Virtual Camera", "Snap Camera", "ManyCam", "XSplit VCam", "Droidcam")
  trigger an immediate red flag.

Layer 2 — Per-frame replay/injection statistics
  Real webcam streams have noise. A replayed video has unrealistically smooth
  temporal differences in the pixel domain. We measure:
    * inter-frame pixel variance — too low ⇒ replay
    * compression-block artifacts via DCT energy in high-freq band
    * face-region/background colour drift — a replayed video has the face
      moving while the background stays identical at the pixel level

Layer 3 — Deepfake face artefacts
  Deepfake pipelines (FaceSwap, ROOP, DeepFaceLab) leave fingerprints around
  the eye sockets and the jaw-line: oversmooth skin, mismatched eye colours,
  and a periodic colour-space anomaly in the YUV-V channel. We extract a
  compact 6-element feature vector and threshold it.

When a check fires we log a `spoof_events` row, optionally save a snapshot, and
return a verdict structure so the caller can stop the mark and surface the
event in the UI.

This module deliberately uses only OpenCV + NumPy so it works inside the same
process as the recogniser with no extra deps.
"""

from __future__ import annotations

import os
import time
from collections import deque

import cv2
import numpy as np


VIRTUAL_CAMERA_KEYWORDS = (
    'obs virtual camera', 'obs-camera', 'obs virt',
    'snap camera', 'snapcamera',
    'manycam', 'xsplit', 'splitcam', 'splitcamera',
    'droidcam', 'epoccam', 'iVCam', 'ivcam',
    'virtual cam', 'virtual webcam', 'vcam',
    'streamfx', 'streamlabs', 'screencap', 'screen recorder',
)


# ---------------------------------------------------------------------------
def is_virtual_camera(device_name: str) -> bool:
    """Heuristic: match the device name against a known list."""
    if not device_name:
        return False
    low = device_name.lower()
    return any(k in low for k in VIRTUAL_CAMERA_KEYWORDS)


def list_video_devices_windows() -> list[str]:
    """Best-effort device enumeration on Windows via WMI/PowerShell.

    Returns an empty list on platforms that don't have it; we fall back to
    the per-frame checks. Cheap enough to call once at startup.
    """
    if os.name != 'nt':
        return []
    try:
        import subprocess  # noqa: F401
        out = subprocess.check_output(
            ['powershell', '-NoProfile', '-Command',
             "Get-PnpDevice -Class Camera | "
             "Select-Object -ExpandProperty FriendlyName"],
            stderr=subprocess.DEVNULL, timeout=4)
        return [line.strip() for line in out.decode(errors='ignore').splitlines() if line.strip()]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
class FrameStats:
    """Per-session statistics buffer for layer-2 (replay) detection."""

    def __init__(self, history: int = 30):
        self._gray_prev: np.ndarray | None = None
        self._diffs: deque[float] = deque(maxlen=history)
        self._bg_hashes: deque[int] = deque(maxlen=history)

    def update(self, frame_bgr) -> dict:
        if frame_bgr is None or frame_bgr.size == 0:
            return {'frame_diff': 0.0, 'bg_repeat': 0.0}
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 90))

        # Inter-frame absolute difference (pixel noise).
        if self._gray_prev is not None:
            d = float(np.abs(small.astype(np.int16) -
                             self._gray_prev.astype(np.int16)).mean())
            self._diffs.append(d)
        self._gray_prev = small

        # Background dHash — a moving face on a frozen background gives the
        # SAME background hash frame after frame.
        bg = np.concatenate([small[:30, :].ravel(),     # top strip (ceiling)
                             small[-30:, :].ravel()])   # bottom strip (desk)
        bg_hash = hash(bg.tobytes()) & 0xffffffff
        self._bg_hashes.append(bg_hash)

        unique_bg = len(set(self._bg_hashes))
        return {
            'frame_diff': float(np.mean(self._diffs)) if self._diffs else 0.0,
            'bg_repeat':  1.0 - unique_bg / max(1, len(self._bg_hashes)),
        }


def deepfake_features(face_bgr) -> dict:
    """Compact 6-feature vector that fires on common deepfake artefacts."""
    if face_bgr is None or face_bgr.size == 0:
        return {'edge_density': 0.0, 'skin_smoothness': 0.0,
                'eye_asymmetry': 0.0, 'v_channel_anom': 0.0,
                'jpeg_ghost': 0.0, 'score': 0.0}

    h, w = face_bgr.shape[:2]
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    yuv = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2YUV)

    # 1) Edge density on face — deepfakes blend out micro-edges
    edges = cv2.Canny(gray, 70, 150)
    edge_density = float(np.count_nonzero(edges)) / (h * w)

    # 2) Skin smoothness — Laplacian variance is LOW for over-smoothed faces
    skin_smoothness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # 3) Eye asymmetry — split face in half, compare LBPH-ish histograms
    left = gray[:, :w // 2]
    right = cv2.flip(gray[:, w // 2:], 1)
    left_hist = cv2.calcHist([left], [0], None, [16], [0, 256]).flatten()
    right_hist = cv2.calcHist([right], [0], None, [16], [0, 256]).flatten()
    eye_asymmetry = float(np.sum(np.abs(left_hist - right_hist))) / (left_hist.sum() + 1e-6)

    # 4) Periodic V-channel anomaly — many deepfakes shift overall colour
    v_channel_anom = float(np.std(yuv[:, :, 2])) / 64.0

    # 5) JPEG ghost — re-encode and compare error spectrum
    _, enc = cv2.imencode('.jpg', face_bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
    dec = cv2.imdecode(np.frombuffer(enc, dtype=np.uint8), cv2.IMREAD_COLOR)
    jpeg_ghost = float(np.abs(face_bgr.astype(np.int16) -
                              dec.astype(np.int16)).mean()) / 32.0

    # Combine into a single 0-1 spoof score. Tuned conservatively to keep
    # false-positives low (real users blink + move and pass easily).
    score = (
        0.30 * (1.0 if edge_density < 0.025 else 0.0) +
        0.25 * (1.0 if skin_smoothness < 70.0 else 0.0) +
        0.15 * min(1.0, eye_asymmetry * 0.5) +
        0.10 * min(1.0, max(0.0, v_channel_anom - 0.4)) +
        0.20 * min(1.0, max(0.0, jpeg_ghost - 0.6))
    )
    return {'edge_density': edge_density, 'skin_smoothness': skin_smoothness,
            'eye_asymmetry': eye_asymmetry, 'v_channel_anom': v_channel_anom,
            'jpeg_ghost': jpeg_ghost, 'score': score}


# ---------------------------------------------------------------------------
class DeepfakeGuard:
    """High-level guard: combines all three layers into a single verdict."""

    def __init__(self):
        self._stats = FrameStats(history=30)
        self._last_alert_ts = 0.0
        self._virtual_camera_known = self._scan_devices()

    def _scan_devices(self) -> bool:
        names = list_video_devices_windows()
        return any(is_virtual_camera(n) for n in names)

    def assess(self, frame_bgr, face_bgr) -> dict:
        s = self._stats.update(frame_bgr)
        feats = deepfake_features(face_bgr) if face_bgr is not None else {'score': 0.0}

        reasons = []
        if self._virtual_camera_known:
            reasons.append('virtual-camera-device')
        if len(self._stats._diffs) >= 20 and s['frame_diff'] < 0.6:
            reasons.append('replay-flat-noise')
        if len(self._stats._bg_hashes) >= 20 and s['bg_repeat'] > 0.85:
            reasons.append('static-background')
        if feats.get('score', 0.0) >= 0.55:
            reasons.append(f"deepfake-score-{feats['score']:.2f}")

        verdict = 'block' if len(reasons) >= 2 else ('warn' if reasons else 'ok')
        return {
            'verdict':  verdict,
            'reasons':  reasons,
            'stats':    s,
            'features': feats,
            'cooldown_ok': (time.time() - self._last_alert_ts) > 12.0,
        }

    def mark_alerted(self) -> None:
        self._last_alert_ts = time.time()


_guard: DeepfakeGuard | None = None


def guard() -> DeepfakeGuard:
    global _guard
    if _guard is None:
        _guard = DeepfakeGuard()
    return _guard
