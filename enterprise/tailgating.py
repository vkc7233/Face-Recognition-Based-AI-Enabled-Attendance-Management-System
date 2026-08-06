"""
N6 — Tailgating + anti-passback detection at the door / turnstile.

Tailgating
----------
A real attendance event has exactly ONE recognised face crossing the gate.
If we see one recognised face + N unknown faces in the same frame (or within
the trailing window), someone is "tailing" the authorised person through the
door. This is what physical-access teams call tailgating; HR-led attendance
tools never check for it.

Anti-passback
-------------
If the same person was last logged as "in" and tries to mark "in" again
without an intermediate "out" event, that's either a re-use attack or a
genuine missed-checkout. We let the operator pick: hard block, soft warn,
or just log.

This module produces verdicts the recogniser consumes and an access_events
row for every detection. The UI surfaces it under /enterprise/access.
"""

from __future__ import annotations

from collections import deque

import db


# ---------------------------------------------------------------------------
class TailgateBuffer:
    """Sliding window of "what was in the frame" used to detect tailgating."""

    def __init__(self, window_frames: int = 8):
        self._known: deque[int] = deque(maxlen=window_frames)
        self._unknown: deque[int] = deque(maxlen=window_frames)

    def push(self, known_count: int, unknown_count: int) -> None:
        self._known.append(known_count)
        self._unknown.append(unknown_count)

    def verdict(self) -> dict:
        if not self._known:
            return {'tailgate': False, 'known_peak': 0, 'unknown_peak': 0}
        kpk = max(self._known)
        upk = max(self._unknown)
        # Tailgate: at least one frame had >=1 recognised + >=1 unknown
        # OR two recognised at once (could be partner card-share)
        tail = (kpk >= 1 and upk >= 1) or kpk >= 2
        return {'tailgate': tail, 'known_peak': kpk, 'unknown_peak': upk}


# ---------------------------------------------------------------------------
def check_antipassback(person_id: str, intended_direction: str) -> dict:
    """Returns {'ok': bool, 'reason': str}."""
    last = db.last_attendance_dir(person_id)
    if not last:
        return {'ok': True, 'reason': 'no-prior-event'}
    if last == intended_direction:
        return {'ok': False, 'reason': f'duplicate-{intended_direction}'}
    return {'ok': True, 'reason': 'normal-toggle'}


def log_tailgate(branch_id, person_id, known_peak: int, unknown_peak: int,
                 snapshot: str = '') -> int:
    detail = f'known_peak={known_peak} unknown_peak={unknown_peak}'
    return db.log_access_event(
        kind='tailgate', branch_id=branch_id, person_id=person_id,
        face_count=known_peak + unknown_peak, direction='in',
        snapshot=snapshot, detail=detail)


def log_antipassback(branch_id, person_id, reason: str) -> int:
    return db.log_access_event(
        kind='antipassback', branch_id=branch_id, person_id=person_id,
        detail=reason)


_buffer = TailgateBuffer()


def buffer() -> TailgateBuffer:
    return _buffer
