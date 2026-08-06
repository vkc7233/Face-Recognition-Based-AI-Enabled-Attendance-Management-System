"""
Token-bucket rate limiting for public endpoints.

Why
---
Anything that takes anonymous traffic — login, /sso/<id>/callback, kiosk
PIN/QR/RFID, /sdk/v1/auth, /webhook/whatsapp, /scim/v2/* — is a brute-force
target. We cap them at a sensible rate per (IP + route), with a small burst.

Tiny, deps-free
---------------
Plain in-memory dict, thread-safe with a lock, evicts entries older than
2× the longest window. Production deployments behind multiple gunicorn
workers can swap in Redis with one method change.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from functools import wraps
from typing import Callable

from flask import g, jsonify, request


_buckets: dict[str, deque[float]] = {}
_lock = threading.Lock()
_GC_AFTER = 1200.0   # seconds — evict idle buckets every 20 minutes


def _client_key(extra: str = '') -> str:
    ip = (request.headers.get('X-Forwarded-For',
                              request.remote_addr or '-').split(',')[0].strip())
    return f'{ip}|{extra or request.path}'


def _hit(key: str, capacity: int, per_seconds: float) -> tuple[bool, int]:
    now = time.monotonic()
    cutoff = now - per_seconds
    with _lock:
        dq = _buckets.get(key)
        if dq is None:
            dq = deque(maxlen=capacity + 1)
            _buckets[key] = dq
        # Drop expired hits
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= capacity:
            retry_after = max(0, int(per_seconds - (now - dq[0])))
            return False, retry_after
        dq.append(now)
        # Cheap incremental GC
        if len(_buckets) > 2000:
            for k in list(_buckets.keys())[:500]:
                if not _buckets[k] or _buckets[k][-1] < now - _GC_AFTER:
                    _buckets.pop(k, None)
        return True, 0


def limit(rate: str, key: str = '') -> Callable:
    """Decorator: ``@limit("30/m")`` or ``"5/10s"``.

    Format: <max>/<count><unit> where unit ∈ {s, m, h}.
    Optional `key` overrides the default `<ip>|<path>` bucket — e.g.
    ``key="kiosk-pin"`` will share a bucket across kiosk_pin endpoints
    so an attacker can't hop between routes.
    """
    cap, _, win = rate.partition('/')
    capacity = int(cap)
    n = ''.join(ch for ch in win if ch.isdigit()) or '1'
    unit = ''.join(ch for ch in win if ch.isalpha()) or 'm'
    seconds = int(n) * {'s': 1, 'm': 60, 'h': 3600}.get(unit, 60)

    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            ok, retry_after = _hit(_client_key(key), capacity, seconds)
            if not ok:
                rid = getattr(g, 'request_id', '-')
                resp = jsonify({'ok': False, 'error': 'rate-limited',
                                'retry_after_sec': retry_after,
                                'request_id': rid})
                resp.status_code = 429
                resp.headers['Retry-After'] = str(retry_after)
                return resp
            return fn(*a, **kw)
        return wrap
    return deco
