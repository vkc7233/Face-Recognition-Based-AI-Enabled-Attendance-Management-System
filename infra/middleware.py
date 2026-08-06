"""
Request-ID + security headers + structured access log.

What this gives you
-------------------
* `g.request_id` — a UUID per request, also pushed to the SIEM audit row and
  the response header `X-Request-ID` so a SOC analyst can trace a click from
  the browser through to the SIEM event in one search.

* Strong defaults for the modern security headers:
    X-Content-Type-Options:    nosniff
    X-Frame-Options:           SAMEORIGIN
    Referrer-Policy:           strict-origin-when-cross-origin
    Permissions-Policy:        camera=(self) geolocation=(self) microphone=()
    Content-Security-Policy:   nonce-based, restrictive
    Strict-Transport-Security: when HTTPS

* Structured single-line JSON access log per response:
    {"ts": "...", "rid": "...", "ip": "...", "method": "...", "path": "...",
     "status": 200, "ms": 12.3, "ua": "...", "actor": "admin"}

  Drop-in for Datadog / Loki / Elastic ingestion.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid

from flask import Flask, Response, g, request

log = logging.getLogger('facemark.access')


# Endpoints that handle their own CSP (kiosk, MJPEG stream, file uploads) and
# don't want the strict default.
_CSP_EXEMPT_PATHS = ('/video_feed', '/sdk/v1/widget.js')


def _build_csp(nonce: str, path: str) -> str:
    if any(path.startswith(p) for p in _CSP_EXEMPT_PATHS):
        # Looser policy for the streaming/embed endpoints
        return (
            "default-src 'self' data: blob:; "
            "img-src 'self' data: blob:; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'self'"
        )
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net 'unsafe-inline'; "
        f"style-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net "
        "https://fonts.googleapis.com 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


def install(app: Flask) -> None:
    @app.before_request
    def _start() -> None:
        g.request_id = (request.headers.get('X-Request-ID')
                        or uuid.uuid4().hex[:16])
        g.csp_nonce  = secrets.token_urlsafe(12)
        g.t0 = time.perf_counter()

    @app.after_request
    def _finish(resp: Response) -> Response:
        # Headers
        resp.headers['X-Request-ID'] = getattr(g, 'request_id', '-')
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
        resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        resp.headers['Permissions-Policy'] = (
            'camera=(self), geolocation=(self), microphone=()')
        # Don't clobber an explicit CSP set by the view.
        resp.headers.setdefault(
            'Content-Security-Policy',
            _build_csp(getattr(g, 'csp_nonce', ''), request.path))
        if request.is_secure or request.headers.get('X-Forwarded-Proto') == 'https':
            resp.headers.setdefault(
                'Strict-Transport-Security',
                'max-age=63072000; includeSubDomains; preload')

        # Structured access log
        try:
            from flask import session
            actor = session.get('admin') or session.get('portal_pid') or '-'
        except Exception:  # noqa: BLE001
            actor = '-'
        ms = (time.perf_counter() - getattr(g, 't0', 0)) * 1000.0
        log.info(json.dumps({
            'ts':     int(time.time()),
            'rid':    getattr(g, 'request_id', '-'),
            'ip':     request.headers.get('X-Forwarded-For',
                                          request.remote_addr or '-').split(',')[0].strip(),
            'method': request.method,
            'path':   request.path,
            'status': resp.status_code,
            'ms':     round(ms, 2),
            'ua':     (request.headers.get('User-Agent') or '')[:80],
            'actor':  actor,
        }))
        return resp


def csp_nonce() -> str:
    """Use this in Jinja: <script nonce="{{ csp_nonce() }}">...</script>"""
    return getattr(g, 'csp_nonce', '')
