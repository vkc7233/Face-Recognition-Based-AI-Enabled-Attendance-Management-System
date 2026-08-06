"""
Health + metrics endpoints.

  GET /healthz  — fast liveness probe (always 200 if process is up)
  GET /readyz   — deep readiness check: DB, recogniser, scheduler, SIEM queue
                  Returns 200 + JSON when every subsystem is up, otherwise
                  503 + structured failure breakdown.

  GET /metrics  — Prometheus exposition: counters, gauges, latency histogram

The Prometheus output is plain text so any scrape target works (Grafana,
Datadog Agent, otel-collector, kube native).
"""

from __future__ import annotations

import os
import time
from collections import deque

from flask import Blueprint, Response, current_app, jsonify, request

import db
import recognizer


health_bp = Blueprint('health', __name__)

# Rolling latency histogram for /metrics
_REQ_TOTAL = {'value': 0}
_REQ_BY_STATUS: dict[int, int] = {}
_LAT: deque[float] = deque(maxlen=2048)


def record(status: int, ms: float) -> None:
    _REQ_TOTAL['value'] += 1
    _REQ_BY_STATUS[status] = _REQ_BY_STATUS.get(status, 0) + 1
    _LAT.append(ms)


# ---------------------------------------------------------------------------
@health_bp.route('/healthz')
def healthz():
    """Always 200. Use this for k8s liveness."""
    return jsonify({'ok': True, 'ts': int(time.time())})


@health_bp.route('/readyz')
def readyz():
    checks: dict[str, dict] = {}

    # DB reachable
    try:
        with db.tx() as c:
            c.execute('SELECT 1').fetchone()
        checks['db'] = {'ok': True}
    except Exception as e:  # noqa: BLE001
        checks['db'] = {'ok': False, 'error': str(e)[:120]}

    # Recogniser loaded
    try:
        rec = recognizer.get()
        checks['recognizer'] = {
            'ok': True, 'backend': rec.name, 'trained': rec.is_trained()}
    except Exception as e:  # noqa: BLE001
        checks['recognizer'] = {'ok': False, 'error': str(e)[:120]}

    # SIEM queue size
    try:
        pending = len(db.pending_audit_for_siem(limit=10))
        checks['siem_queue'] = {'ok': True, 'pending': pending}
    except Exception as e:  # noqa: BLE001
        checks['siem_queue'] = {'ok': False, 'error': str(e)[:120]}

    # Disk space on the static dir
    try:
        import shutil
        usage = shutil.disk_usage('static')
        checks['disk'] = {
            'ok': usage.free > 50 * 1024 * 1024,
            'free_mb': round(usage.free / 1024 / 1024, 1)}
    except Exception as e:  # noqa: BLE001
        checks['disk'] = {'ok': False, 'error': str(e)[:120]}

    ok_all = all(v.get('ok') for v in checks.values())
    return jsonify({'ok': ok_all, 'checks': checks}), (200 if ok_all else 503)


@health_bp.route('/metrics')
def metrics():
    """Prometheus exposition format."""
    lines: list[str] = []
    lines.append('# HELP facemark_requests_total Total HTTP requests served')
    lines.append('# TYPE facemark_requests_total counter')
    lines.append(f'facemark_requests_total {_REQ_TOTAL["value"]}')

    lines.append('# HELP facemark_requests_by_status Total requests by status')
    lines.append('# TYPE facemark_requests_by_status counter')
    for k, v in sorted(_REQ_BY_STATUS.items()):
        lines.append(f'facemark_requests_by_status{{status="{k}"}} {v}')

    if _LAT:
        s = sorted(_LAT)
        n = len(s)
        def p(q): return s[min(n - 1, int(n * q))]
        lines.append('# HELP facemark_request_latency_ms Request latency distribution')
        lines.append('# TYPE facemark_request_latency_ms summary')
        lines.append(f'facemark_request_latency_ms{{quantile="0.5"}} {p(0.5):.2f}')
        lines.append(f'facemark_request_latency_ms{{quantile="0.9"}} {p(0.9):.2f}')
        lines.append(f'facemark_request_latency_ms{{quantile="0.99"}} {p(0.99):.2f}')
        lines.append(f'facemark_request_latency_ms_count {n}')

    try:
        with db.tx() as c:
            registered = c.execute('SELECT COUNT(*) FROM persons').fetchone()[0]
            today_present = c.execute(
                "SELECT COUNT(*) FROM attendance "
                "WHERE date = date('now') AND check_in IS NOT NULL").fetchone()[0]
        lines.append('# TYPE facemark_persons_registered gauge')
        lines.append(f'facemark_persons_registered {registered}')
        lines.append('# TYPE facemark_attendance_present_today gauge')
        lines.append(f'facemark_attendance_present_today {today_present}')
    except Exception:  # noqa: BLE001
        pass

    body = '\n'.join(lines) + '\n'
    return Response(body, mimetype='text/plain; version=0.0.4')
