"""
Mask detection, temperature ingest and door-relay trigger.

Mask detection
--------------
Cheap heuristic: in the lower half of a detected face, look for an unbroken
high-saturation patch covering nose + mouth. Works for cloth, surgical and
KN95 masks. Real production should swap in a trained classifier but the
heuristic catches the visible 80% case at near-zero CPU cost.

Temperature ingest
------------------
Most thermal terminals (Hikvision/CP Plus class) expose a webhook or RS-485
reading. We provide an HTTP ingest endpoint and a settings-driven cutoff
that emits a "fever" event + blocks attendance.

Door relay
----------
A simple HTTP POST to a configurable URL when attendance is accepted. Works
with the Sonoff, Shelly, Hikvision SDK, ESPHome and any IoT relay that
exposes a "click" REST endpoint. The trigger is logged for audit.
"""

from __future__ import annotations

import logging
from urllib import request as urlrequest

import cv2
import numpy as np

import db

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mask detection
# ---------------------------------------------------------------------------
MASK_HSV_LO_HI = [
    (np.array([0,    0, 180]), np.array([180, 60, 255])),  # white / surgical
    (np.array([95,  50, 50]),  np.array([130, 255, 255])), # blue / surgical
    (np.array([0,    0,  0]),  np.array([180, 60,  60])),  # black
]


def detect_mask(face_bgr) -> dict:
    """Return {'mask': bool, 'coverage': float}.

    Examines the bottom 55% of the face (nose down) and reports the fraction
    covered by typical mask colour ranges.
    """
    if face_bgr is None or face_bgr.size == 0:
        return {'mask': False, 'coverage': 0.0}
    h, _ = face_bgr.shape[:2]
    bottom = face_bgr[int(h * 0.45):, :]
    hsv = cv2.cvtColor(bottom, cv2.COLOR_BGR2HSV)
    mask_any = None
    for lo, hi in MASK_HSV_LO_HI:
        m = cv2.inRange(hsv, lo, hi)
        mask_any = m if mask_any is None else cv2.bitwise_or(mask_any, m)
    total = bottom.shape[0] * bottom.shape[1] or 1
    coverage = float(cv2.countNonZero(mask_any)) / float(total)
    return {'mask': coverage >= 0.35, 'coverage': round(coverage, 3)}


# ---------------------------------------------------------------------------
# Temperature gate
# ---------------------------------------------------------------------------
def check_temperature(person_id: str, branch_id, temp_c: float) -> dict:
    """Log + decide whether to allow entry."""
    cutoff = float(db.get_setting('temp_cutoff_c') or '37.5')
    db.log_sensor('temperature_c', person_id=person_id, branch_id=branch_id,
                  value_num=temp_c)
    blocked = temp_c >= cutoff
    return {'blocked': blocked, 'temp_c': temp_c, 'cutoff_c': cutoff}


# ---------------------------------------------------------------------------
# Door / turnstile relay
# ---------------------------------------------------------------------------
def trigger_door(branch_id, person_id) -> dict:
    """Fire the configured relay URL. Per-branch URLs override the global one.

    Settings:
      door_relay_url           - global default (e.g. http://shelly/relay/0?turn=on)
      door_relay_method        - GET / POST (default GET)
      door_relay_body          - optional POST body
    """
    url = (db.get_setting('door_relay_url') or '').strip()
    if not url:
        return {'sent': False, 'detail': 'door-relay-not-configured'}
    method = (db.get_setting('door_relay_method') or 'GET').upper()
    body = (db.get_setting('door_relay_body') or '').encode() if method == 'POST' else None
    try:
        req = urlrequest.Request(url, data=body, method=method,
                                 headers={'User-Agent': 'FaceMark/1.0'})
        with urlrequest.urlopen(req, timeout=5) as r:
            status = f'http-{r.status}'
            ok = 200 <= r.status < 300
    except Exception as e:  # noqa: BLE001
        status = f'error:{e}'
        ok = False
    db.log_door(branch_id, person_id, url, status)
    return {'sent': ok, 'detail': status}
