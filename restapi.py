"""
REST API blueprint for FaceMark.

Surface
-------
GET    /api/v1/health
GET    /api/v1/persons
POST   /api/v1/persons
GET    /api/v1/persons/<pid>
DELETE /api/v1/persons/<pid>
GET    /api/v1/attendance?date=YYYY-MM-DD
POST   /api/v1/attendance/check_in
POST   /api/v1/attendance/check_out
GET    /api/v1/branches
GET    /api/v1/shifts
GET    /api/v1/leaves
POST   /api/v1/leaves
GET    /api/v1/notifications
POST   /api/v1/webhooks/test

All endpoints require an API key header: `Authorization: Bearer fm_...`.
Mutating endpoints require the `write` scope.

Webhook delivery is a fire-and-forget HTTP POST signed with HMAC-SHA256 in
the `X-FaceMark-Signature` header so the receiver can verify authenticity.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import threading
import time
from datetime import date as _date
from functools import wraps
from urllib import request as urlrequest

from flask import Blueprint, current_app, jsonify, request

import db


api_bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')


# ---------------------------------------------------------------------------
def _need_scope(scope: str):
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            auth = request.headers.get('Authorization', '')
            raw = auth.split(' ', 1)[1].strip() if auth.startswith('Bearer ') else ''
            row = db.verify_api_key(raw)
            if not row:
                return jsonify({'ok': False, 'error': 'invalid-api-key'}), 401
            scopes = (row.get('scopes') or '').split(',')
            if scope not in scopes and 'admin' not in scopes:
                return jsonify({'ok': False, 'error': f'scope-required:{scope}'}), 403
            return fn(*a, **kw)
        return inner
    return deco


# ---------------------------------------------------------------------------
@api_bp.route('/health')
def health():
    return jsonify({'ok': True, 'ts': int(time.time())})


@api_bp.route('/persons')
@_need_scope('read')
def list_persons():
    rows = db.list_persons()
    return jsonify({'ok': True, 'persons': [dict(r) for r in rows]})


@api_bp.route('/persons/<pid>')
@_need_scope('read')
def get_person(pid):
    r = db.get_person(pid)
    if not r:
        return jsonify({'ok': False, 'error': 'not-found'}), 404
    return jsonify({'ok': True, 'person': dict(r)})


@api_bp.route('/persons', methods=['POST'])
@_need_scope('write')
def create_person():
    j = request.get_json(force=True, silent=True) or {}
    pid = (j.get('person_id') or '').strip()
    name = (j.get('name') or '').strip()
    if not pid or not name:
        return jsonify({'ok': False, 'error': 'person_id+name required'}), 400
    db.upsert_person(pid, name, j.get('department_id'),
                     j.get('email'), j.get('guardian_email'),
                     j.get('date_of_birth'))
    return jsonify({'ok': True, 'person_id': pid})


@api_bp.route('/persons/<pid>', methods=['DELETE'])
@_need_scope('write')
def delete_person(pid):
    db.delete_person(pid)
    return jsonify({'ok': True})


@api_bp.route('/attendance')
@_need_scope('read')
def attendance():
    d = request.args.get('date') or _date.today().isoformat()
    return jsonify({'ok': True, 'date': d, 'rows': db.list_attendance(d)})


@api_bp.route('/attendance/check_in', methods=['POST'])
@_need_scope('write')
def check_in():
    j = request.get_json(force=True, silent=True) or {}
    pid = (j.get('person_id') or '').strip()
    if not pid:
        return jsonify({'ok': False, 'error': 'person_id required'}), 400
    ws = db.get_setting('work_start_time', '09:00')
    lt = int(db.get_setting('late_threshold_min', '15'))
    res = db.manual_check_in(pid, ws, lt)
    dispatch_event('check_in', {'person_id': pid, **res})
    return jsonify({'ok': True, **res})


@api_bp.route('/attendance/check_out', methods=['POST'])
@_need_scope('write')
def check_out():
    j = request.get_json(force=True, silent=True) or {}
    pid = (j.get('person_id') or '').strip()
    if not pid:
        return jsonify({'ok': False, 'error': 'person_id required'}), 400
    res = db.manual_check_out(pid)
    dispatch_event('check_out', {'person_id': pid, **res})
    return jsonify({'ok': True, **res})


@api_bp.route('/branches')
@_need_scope('read')
def branches():
    return jsonify({'ok': True, 'branches': db.list_branches()})


@api_bp.route('/shifts')
@_need_scope('read')
def shifts():
    return jsonify({'ok': True, 'shifts': db.list_shifts()})


@api_bp.route('/leaves')
@_need_scope('read')
def leaves():
    return jsonify({'ok': True, 'leaves': db.list_leaves(
        request.args.get('status'))})


@api_bp.route('/leaves', methods=['POST'])
@_need_scope('write')
def create_leave():
    j = request.get_json(force=True, silent=True) or {}
    lid = db.add_leave(j['person_id'], j['leave_type'],
                       j['start_date'], j['end_date'], j.get('reason', ''))
    return jsonify({'ok': True, 'id': lid})


@api_bp.route('/notifications')
@_need_scope('read')
def notif():
    return jsonify({'ok': True, 'rows': db.list_notifications(200)})


@api_bp.route('/webhooks/test', methods=['POST'])
@_need_scope('admin')
def webhooks_test():
    dispatch_event('test', {'msg': 'hello-from-facemark'})
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Webhook delivery (signed POST)
# ---------------------------------------------------------------------------
def _sign(body: bytes, secret: str) -> str:
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def dispatch_event(event: str, payload: dict) -> None:
    """Fire-and-forget delivery to every enabled webhook subscribed to `event`."""
    try:
        hooks = db.list_webhooks()
    except Exception:  # noqa: BLE001
        return
    body = json.dumps({'event': event, 'data': payload,
                       'ts': int(time.time())}).encode()
    for h in hooks:
        if not h['enabled']:
            continue
        events = (h['events'] or '').split(',')
        if event not in events and '*' not in events:
            continue
        threading.Thread(
            target=_send_webhook,
            args=(h['id'], h['url'], body, h.get('secret') or ''),
            daemon=True, name=f'wh-{h["id"]}'
        ).start()


def _send_webhook(hook_id: int, url: str, body: bytes, secret: str) -> None:
    try:
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'FaceMark-Webhook/1.0',
        }
        if secret:
            headers['X-FaceMark-Signature'] = _sign(body, secret)
        req = urlrequest.Request(url, data=body, method='POST', headers=headers)
        with urlrequest.urlopen(req, timeout=8) as r:
            db.update_webhook_status(hook_id, f'http-{r.status}')
    except Exception as e:  # noqa: BLE001
        db.update_webhook_status(hook_id, f'error:{e}')
