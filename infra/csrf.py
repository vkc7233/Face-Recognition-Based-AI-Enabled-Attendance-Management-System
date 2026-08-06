"""
Minimal CSRF protection for the admin UI.

Approach
--------
* On any state-changing request (POST/PUT/PATCH/DELETE) that is NOT in the
  public API surface (`/api/v1/`, `/scim/v2/`, `/sdk/v1/`, kiosk endpoints,
  webhook endpoints) — require a `csrf_token` form field or `X-CSRF-Token`
  header that matches the session-bound token.
* The token is regenerated per session and exposed to Jinja via the
  `csrf_token()` template global.

This trades 0 third-party deps for the Flask-WTF-style decorator pattern.
"""

from __future__ import annotations

import hmac
import os
import secrets

from flask import Flask, abort, g, request, session


# Routes (by prefix) that may NOT carry a session cookie and authenticate by
# token instead. They must NOT be CSRF-checked.
_EXEMPT_PREFIXES = (
    '/api/v1/', '/scim/v2/', '/sdk/v1/',
    '/kiosk/pin', '/kiosk/qr', '/kiosk/rfid',
    '/webhook/whatsapp',
    '/enterprise/slack/command',
    '/enterprise/teams/webhook',
    '/enterprise/presence/',
    '/enterprise/sensor/',
    '/enterprise/muster/',
    '/enterprise/occupancy/',
    '/api/transport/board',
    '/api/sensor/temperature',
    '/api/gps/check_in',
    '/enterprise/sso/',         # OIDC callback uses state, not CSRF
)


def _token() -> str:
    tok = session.get('_csrf')
    if not tok:
        tok = secrets.token_urlsafe(24)
        session['_csrf'] = tok
    return tok


def install(app: Flask) -> None:
    @app.before_request
    def _check() -> None:
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return
        if any(request.path.startswith(p) for p in _EXEMPT_PREFIXES):
            return
        if os.environ.get('FACEMARK_TEST_BYPASS_CSRF') == '1':
            return
        provided = (request.headers.get('X-CSRF-Token')
                    or request.form.get('csrf_token') or '')
        expected = _token()
        if not provided or not hmac.compare_digest(provided, expected):
            abort(400, description='csrf-token-missing-or-invalid')

    @app.context_processor
    def _inject() -> dict:
        return {'csrf_token': _token}
