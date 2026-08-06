"""
N10 — Wi-Fi / VPN / calendar presence sync.

Infers "the employee is at the office" from passive corporate signals so we
don't force everyone past a camera.

Three sources
-------------
  wifi      — push from the corp Wi-Fi controller (Aruba/Ruckus/Cisco) or a
              small agent on the laptop reporting the current SSID. If the
              SSID is in `corp_wifi_ssids` (settings, csv), credit presence.
  vpn       — call to /enterprise/presence/vpn with a username and active=true
              when the corporate VPN gateway opens a session.
  calendar  — Outlook/Google webhook posts events with attendees + locations.
              If location matches a known branch and the user is the organiser
              or accepted, we credit presence for the meeting window.

A single attendance check-in is created when total score for the day crosses a
configurable threshold (default 0.6).
"""

from __future__ import annotations

import db


# ---------------------------------------------------------------------------
def ingest_wifi(person_id: str, ssid: str, ap: str = '') -> dict:
    ssids = [s.strip() for s in (db.get_setting('corp_wifi_ssids') or '').split(',') if s.strip()]
    if not ssids or ssid not in ssids:
        return {'credited': False, 'reason': 'ssid-not-in-corp-list'}
    db.log_presence(person_id, 'wifi', detail=f'ssid={ssid} ap={ap}', score=0.4)
    return maybe_mark(person_id)


def ingest_vpn(person_id: str, active: bool, gateway: str = '') -> dict:
    if not active:
        return {'credited': False, 'reason': 'inactive'}
    db.log_presence(person_id, 'vpn', detail=f'gw={gateway}', score=0.35)
    return maybe_mark(person_id)


def ingest_calendar(person_id: str, location: str = '',
                    subject: str = '') -> dict:
    branch_hit = False
    for b in db.list_branches():
        if location and b['name'].lower() in (location or '').lower():
            branch_hit = True
            break
    score = 0.5 if branch_hit else 0.15
    db.log_presence(person_id, 'calendar',
                    detail=f'loc={location} subj={subject}', score=score)
    return maybe_mark(person_id)


def maybe_mark(person_id: str) -> dict:
    """If today's total presence score >= threshold, mark attendance."""
    threshold = float(db.get_setting('presence_threshold') or '0.6')
    with db.tx() as c:
        r = c.execute(
            "SELECT COALESCE(SUM(score), 0) AS s FROM presence_signals "
            "WHERE person_id = ? AND date(seen_at) = date('now')",
            (person_id,)).fetchone()
    total = float(r['s'])
    if total < threshold:
        return {'credited': False, 'total': total, 'threshold': threshold}
    ws = db.get_setting('work_start_time', '09:00')
    lt = int(db.get_setting('late_threshold_min', '15'))
    res = db.mark_attendance(person_id, ws, lt,
                             int(db.get_setting('min_checkout_gap_min', '30')))
    return {'credited': True, 'total': total, 'event': res.get('event')}
