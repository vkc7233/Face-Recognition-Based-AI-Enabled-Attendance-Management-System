"""
Tiny in-memory TTL cache with a Flask-friendly decorator.

Use case
--------
Expensive read-only computations that don't need to be re-derived per request
(competitor matrix, GTM pages, the hub-page stat strip, the at-risk and
burnout dashboards). One call, cache the result for `ttl` seconds, every
follow-up read is O(1).

The cache key combines the function name, positional args, and the sorted
items of kwargs. Per-tenant data is keyed cleanly when callers pass a
`tenant_id` argument.
"""

from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Any, Callable


_store: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def make_key(*a, **kw) -> str:
    return repr(a) + '|' + repr(sorted(kw.items()))


def memoize(ttl: int = 60) -> Callable:
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            now = time.monotonic()
            k = fn.__qualname__ + '|' + make_key(*a, **kw)
            with _lock:
                hit = _store.get(k)
                if hit and hit[0] > now:
                    return hit[1]
            val = fn(*a, **kw)
            with _lock:
                _store[k] = (now + ttl, val)
                # Lazy GC: cap entries
                if len(_store) > 4000:
                    for kk in list(_store.keys())[:1000]:
                        v = _store.get(kk)
                        if v and v[0] < now:
                            _store.pop(kk, None)
            return val
        wrap.invalidate = lambda: _store.clear()
        return wrap
    return deco


def clear() -> None:
    with _lock:
        _store.clear()


def size() -> int:
    return len(_store)
