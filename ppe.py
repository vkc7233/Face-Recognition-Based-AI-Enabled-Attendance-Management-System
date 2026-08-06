"""
Lightweight PPE detection for the Construction Site Edition.

Real production should swap in a YOLOv8 / RT-DETR model trained on hard-hat
and high-visibility-vest data. This module provides a colour + region
heuristic that runs at >30 fps on a Raspberry Pi class device and produces
"likely missing helmet/vest" alerts good enough to flag obvious violations
at the gate. The detector keeps the same public interface as a future ML
backend so the swap is one-line.

Public API:
    detect(frame_bgr, face_bbox)  ->  {'helmet': bool, 'vest': bool, 'reasons': []}
"""

from __future__ import annotations

import cv2
import numpy as np


# HSV ranges for typical safety-helmet and high-vis colours.
HELMET_HUES = {
    'yellow':  (np.array([15,  90, 120]), np.array([40, 255, 255])),
    'orange':  (np.array([5,   90, 120]), np.array([20, 255, 255])),
    'white':   (np.array([0,    0, 180]), np.array([180, 50, 255])),
    'red':     (np.array([0,   90, 100]), np.array([10, 255, 255])),
    'blue':    (np.array([95,  80,  80]), np.array([130, 255, 255])),
}
VEST_HUES = {
    'orange':  (np.array([5,   90, 120]), np.array([20, 255, 255])),
    'yellow':  (np.array([20, 100, 120]), np.array([40, 255, 255])),
    'lime':    (np.array([40, 100, 120]), np.array([75, 255, 255])),
}

HELMET_COVERAGE_MIN = 0.20   # at least 20% of the head ROI must be a helmet colour
VEST_COVERAGE_MIN = 0.18


def _coverage(hsv_roi, ranges: dict) -> float:
    if hsv_roi.size == 0:
        return 0.0
    total_pixels = hsv_roi.shape[0] * hsv_roi.shape[1]
    if total_pixels == 0:
        return 0.0
    mask_any = None
    for lo, hi in ranges.values():
        m = cv2.inRange(hsv_roi, lo, hi)
        mask_any = m if mask_any is None else cv2.bitwise_or(mask_any, m)
    return float(cv2.countNonZero(mask_any)) / float(total_pixels)


def detect(frame_bgr, face_bbox) -> dict:
    """Run PPE check around a known face bounding box.

    head ROI: above the face, ~50% taller and 20% wider.
    body ROI: torso below the face (~face_w * 2 wide, face_h * 2.5 tall).
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return {'helmet': False, 'vest': False, 'reasons': ['no-frame']}

    h, w = frame_bgr.shape[:2]
    fx, fy, fw, fh = face_bbox
    fx = max(0, int(fx)); fy = max(0, int(fy))
    fw = max(1, int(fw)); fh = max(1, int(fh))

    # head ROI
    pad_x = int(fw * 0.20)
    head_top = max(0, fy - int(fh * 0.6))
    head_x0 = max(0, fx - pad_x)
    head_x1 = min(w, fx + fw + pad_x)
    head_y0, head_y1 = head_top, max(head_top + 1, fy)
    head_roi = frame_bgr[head_y0:head_y1, head_x0:head_x1]

    # body ROI
    body_x0 = max(0, fx - int(fw * 0.5))
    body_x1 = min(w, fx + fw + int(fw * 0.5))
    body_y0 = min(h, fy + fh)
    body_y1 = min(h, body_y0 + int(fh * 2.5))
    body_roi = frame_bgr[body_y0:body_y1, body_x0:body_x1]

    reasons = []
    helmet_ok = False
    vest_ok = False
    if head_roi.size:
        hsv = cv2.cvtColor(head_roi, cv2.COLOR_BGR2HSV)
        cov = _coverage(hsv, HELMET_HUES)
        helmet_ok = cov >= HELMET_COVERAGE_MIN
        if not helmet_ok:
            reasons.append(f'helmet_coverage={cov:.2f}')
    else:
        reasons.append('no-head-roi')

    if body_roi.size:
        hsv = cv2.cvtColor(body_roi, cv2.COLOR_BGR2HSV)
        cov = _coverage(hsv, VEST_HUES)
        vest_ok = cov >= VEST_COVERAGE_MIN
        if not vest_ok:
            reasons.append(f'vest_coverage={cov:.2f}')
    else:
        reasons.append('no-body-roi')

    return {'helmet': helmet_ok, 'vest': vest_ok, 'reasons': reasons}
