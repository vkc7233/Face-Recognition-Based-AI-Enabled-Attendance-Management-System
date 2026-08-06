"""
N3 — SIEM-grade audit streaming.

We push enterprise-audit rows (auth, admin actions, role changes, MFA resets,
impersonations) to Splunk / Datadog / Elastic / Sentinel via HTTPS in near
real-time. Tiered retention is applied locally:

  category       default retention
  ─────────────  ────────────────────
  auth           12 months
  admin          24 months
  impersonation  36 months
  mfa            36 months
  other          90 days

Both numbers are configurable in Settings.

The streamer runs as a small thread that wakes every 15s, drains
audit_extended rows where streamed=0, fans them out to every enabled sink,
and marks them as streamed when at least one sink confirmed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from urllib import request as urlrequest

import db

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
RETENTION_DAYS = {
    'auth':          365,
    'admin':         365 * 2,
    'impersonation': 365 * 3,
    'mfa':           365 * 3,
    'other':         90,
}


def cef_format(row: dict) -> str:
    """ArcSight CEF for legacy SIEMs."""
    return (
        f"CEF:0|FaceMark|Attendance|1.0|{row['category']}|{row['action']}|5|"
        f"act={row['action']} suser={row.get('actor') or '-'} "
        f"duser={row.get('target') or '-'} msg={row.get('detail') or ''} "
        f"src={row.get('ip') or '-'} requestClientApplication={row.get('user_agent') or '-'}"
    )


def leef_format(row: dict) -> str:
    """QRadar LEEF."""
    return (
        f"LEEF:2.0|FaceMark|Attendance|1.0|{row['action']}|"
        f"cat={row['category']}\tdevTime={row['created_at']}\tusrName={row.get('actor') or '-'}\t"
        f"dst={row.get('target') or '-'}\tsrc={row.get('ip') or '-'}\tmsg={row.get('detail') or ''}"
    )


def to_event(row: dict, fmt: str) -> bytes:
    if fmt == 'cef':
        return cef_format(row).encode()
    if fmt == 'leef':
        return leef_format(row).encode()
    return json.dumps({
        '@timestamp': row['created_at'],
        'category':   row['category'],
        'action':     row['action'],
        'actor':      row.get('actor'),
        'target':     row.get('target'),
        'detail':     row.get('detail'),
        'ip':         row.get('ip'),
        'user_agent': row.get('user_agent'),
    }).encode()


def push_to_sink(sink: dict, rows: list[dict]) -> tuple[bool, str]:
    if not rows:
        return True, 'nothing-to-send'
    # Splunk HEC supports many JSON events newline-separated; CEF/LEEF likewise
    body = b'\n'.join(to_event(r, sink.get('fmt', 'json')) for r in rows)
    headers = {'Content-Type': 'application/json'
               if sink.get('fmt', 'json') == 'json' else 'text/plain'}
    if sink.get('auth_header'):
        # e.g. "Authorization: Splunk <token>" or "Authorization: Bearer …"
        k, _, v = sink['auth_header'].partition(':')
        headers[k.strip()] = v.strip()
    try:
        req = urlrequest.Request(sink['url'], data=body, method='POST',
                                 headers=headers)
        with urlrequest.urlopen(req, timeout=8) as r:
            ok = 200 <= r.status < 300
            return ok, f'http-{r.status}'
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:120]


# ---------------------------------------------------------------------------
_started = False
_lock = threading.Lock()


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        threading.Thread(target=_loop, daemon=True,
                         name='facemark-siem').start()
        _started = True


def _loop() -> None:
    while True:
        try:
            drain_once()
        except Exception as e:  # noqa: BLE001
            log.warning('siem drain: %s', e)
        time.sleep(15)


def drain_once() -> dict:
    sinks = db.list_siem_sinks(enabled_only=True)
    rows = db.pending_audit_for_siem(limit=200)
    if not rows or not sinks:
        return {'rows': len(rows), 'sinks': len(sinks)}
    any_ok = False
    for s in sinks:
        ok, detail = push_to_sink(s, rows)
        db.update_siem_sink_status(s['id'], 'ok' if ok else f'error:{detail}')
        if ok:
            any_ok = True
    if any_ok:
        db.mark_audit_streamed([r['id'] for r in rows])
    return {'rows': len(rows), 'sinks': len(sinks)}


def apply_retention() -> dict:
    out = {}
    rv = int(db.get_setting('retention_audit_auth_days')   or RETENTION_DAYS['auth'])
    ra = int(db.get_setting('retention_audit_admin_days')  or RETENTION_DAYS['admin'])
    ri = int(db.get_setting('retention_audit_imp_days')    or RETENTION_DAYS['impersonation'])
    rm = int(db.get_setting('retention_audit_mfa_days')    or RETENTION_DAYS['mfa'])
    ro = int(db.get_setting('retention_audit_other_days') or RETENTION_DAYS['other'])
    out['auth']          = db.purge_audit_by_category('auth', rv)
    out['admin']         = db.purge_audit_by_category('admin', ra)
    out['impersonation'] = db.purge_audit_by_category('impersonation', ri)
    out['mfa']           = db.purge_audit_by_category('mfa', rm)
    out['other']         = db.purge_audit_by_category('other', ro)
    return out
