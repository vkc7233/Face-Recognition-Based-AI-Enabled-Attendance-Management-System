"""
Continuous-presence verification for exams / secure shifts.

When an exam session is active and a camera is in `exam` mode, the
`gen_frames` loop calls `exam_mode.observe()` on every frame. The module:

  1. Tracks the set of *expected* people (enrolled in the department/branch
     associated with the exam).
  2. Builds a "currently seen" set from recognised faces in the frame.
  3. Every `check_every_sec` seconds raises an `exam_alerts` row for:
       - `missing`  - expected person not seen for > 90 s
       - `imposter` - unknown face large+near the camera (possible impersonator)
       - `phone`    - a darker reflective rectangle held near the face
                       (very rough; logs `kind=phone` for review)

The aggregator is intentionally lightweight: one in-memory dict keyed on
exam_id, pruned when the exam ends.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

import cv2
import numpy as np

import db

# Per-exam state: { exam_id: { person_id: last_seen_ts } }
_seen: dict[int, dict[str, float]] = defaultdict(dict)
_last_check: dict[int, float] = {}
_alerted_missing: dict[int, set[str]] = defaultdict(set)
_alerted_imposter: dict[int, float] = {}


# ---------------------------------------------------------------------------
def observe(exam: dict, recognised_pids: list[str],
            unknown_faces: list[tuple]) -> list[dict]:
    """Update state and return any new alerts produced this tick."""
    eid = exam['id']
    now = time.time()
    for pid in recognised_pids:
        _seen[eid][pid] = now

    alerts: list[dict] = []

    # Throttle the heavy expected-set check
    if now - _last_check.get(eid, 0) < float(exam.get('check_every_sec') or 60):
        return alerts
    _last_check[eid] = now

    expected_pids = _expected_pids(exam)
    threshold_ts = now - 90.0
    for pid in expected_pids:
        if _seen[eid].get(pid, 0) < threshold_ts and pid not in _alerted_missing[eid]:
            db.log_exam_alert(eid, pid, 'missing', detail='no sighting in last 90s')
            _alerted_missing[eid].add(pid)
            alerts.append({'kind': 'missing', 'person_id': pid})

    # Imposter / extra-face heuristic - we only log once per minute
    if unknown_faces and now - _alerted_imposter.get(eid, 0) > 60:
        biggest = max(unknown_faces, key=lambda f: f[2] * f[3])
        if biggest[2] * biggest[3] > 30000:  # close to the camera
            db.log_exam_alert(eid, 'unknown', 'imposter',
                              detail=f'large unknown bbox {biggest}')
            _alerted_imposter[eid] = now
            alerts.append({'kind': 'imposter'})

    return alerts


def detect_phone_held(face_bgr) -> bool:
    """Very rough: looks for a low-saturation, low-brightness rectangle in the
    bottom right of the face crop."""
    if face_bgr is None or face_bgr.size == 0:
        return False
    h, w = face_bgr.shape[:2]
    roi = face_bgr[int(h * 0.5):, int(w * 0.5):]
    if roi.size == 0:
        return False
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    dark = cv2.inRange(hsv, np.array([0, 0, 0]), np.array([180, 60, 80]))
    return float(cv2.countNonZero(dark)) / float(roi.shape[0] * roi.shape[1]) > 0.30


def end_session(exam_id: int) -> None:
    _seen.pop(exam_id, None)
    _last_check.pop(exam_id, None)
    _alerted_missing.pop(exam_id, None)
    _alerted_imposter.pop(exam_id, None)


# ---------------------------------------------------------------------------
def _expected_pids(exam: dict) -> set[str]:
    """Roster for the exam = persons in the assigned department (or whole org)."""
    dept_id = exam.get('department_id')
    rows = db.list_persons(int(dept_id) if dept_id else None)
    return {r['person_id'] for r in rows}
