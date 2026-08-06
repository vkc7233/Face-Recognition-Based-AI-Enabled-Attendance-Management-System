"""
N15 — Prebuilt HRMS / payroll connectors + iPaaS.

A pluggable connector framework: each kind translates the FaceMark payroll
JSON shape into the target system's expected POST body and pushes it on a
schedule or on-demand. The `Zapier` and `make.com` kinds are generic HTTP
sinks that work with any iPaaS, so the same code covers thousands of tools.

Supported kinds out of the box:
  sap_sf     - SAP SuccessFactors        (OData time records)
  workday    - Workday Time Tracking      (REST)
  adp        - ADP Workforce Now          (REST)
  bamboo     - BambooHR Time Tracking     (REST)
  zoho_people- Zoho People Attendance     (REST)
  keka       - Keka Attendance            (REST)
  greythr    - greytHR Attendance         (REST)
  zapier     - Generic Zapier webhook     (any iPaaS)
  make       - Generic make.com webhook   (any iPaaS)
"""

from __future__ import annotations

import json
import logging
from datetime import date
from urllib import request as urlrequest

import db
import payroll

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
def adapt_to_kind(kind: str, rows: list[dict],
                  start: str, end: str) -> dict:
    """Format the payroll rows into the body the target API expects."""
    if kind == 'bamboo':
        return {'rows': [
            {'employeeId': r['person_id'], 'startDate': start, 'endDate': end,
             'hours': r['worked_hours'], 'overtime': r['overtime_hours']}
            for r in rows
        ]}
    if kind == 'workday':
        return {'TimeRecords': [
            {'workerId': r['person_id'],
             'dateRange': {'start': start, 'end': end},
             'regularHours': r['worked_hours'],
             'overtimeHours': r['overtime_hours']}
            for r in rows]}
    if kind == 'adp':
        return {'events': [
            {'eventType': 'time-record',
             'data': {'employeeId': r['person_id'],
                      'startDate': start, 'endDate': end,
                      'regularHours': r['worked_hours'],
                      'overtimeHours': r['overtime_hours']}}
            for r in rows]}
    if kind == 'sap_sf':
        return {'EmployeeTime': [
            {'externalCode': f"FM-{r['person_id']}-{start}-{end}",
             'userId': r['person_id'],
             'startDate': start, 'endDate': end,
             'quantityInHours': r['worked_hours']} for r in rows]}
    if kind in ('zoho_people', 'keka', 'greythr'):
        return {'records': [
            {'employee_id': r['person_id'],
             'from': start, 'to': end,
             'worked_hours': r['worked_hours'],
             'overtime_hours': r['overtime_hours'],
             'late_days': r['late'], 'absent_days': r['absent']}
            for r in rows]}
    # zapier/make/anything else — raw FaceMark JSON
    return {'org': db.get_setting('org_name') or 'FaceMark',
            'period': {'from': start, 'to': end},
            'rows': rows}


# ---------------------------------------------------------------------------
def sync_one(connector: dict, start: str, end: str) -> dict:
    """Push the period [start,end] to one connector."""
    rows = payroll.compute(start, end)
    body = adapt_to_kind(connector['kind'], rows, start, end)
    raw = json.dumps(body).encode()

    headers = {'Content-Type': 'application/json',
               'User-Agent': 'FaceMark/1.0'}
    if connector.get('api_key'):
        headers['Authorization'] = f"Bearer {connector['api_key']}"

    url = connector.get('endpoint') or ''
    if not url:
        return {'ok': False, 'detail': 'no-endpoint'}

    try:
        req = urlrequest.Request(url, data=raw, method='POST', headers=headers)
        with urlrequest.urlopen(req, timeout=20) as r:
            ok = 200 <= r.status < 300
            detail = f'http-{r.status}'
    except Exception as e:  # noqa: BLE001
        ok, detail = False, str(e)

    db.update_connector_status(connector['id'],
                               'ok' if ok else f'error:{detail[:120]}')
    return {'ok': ok, 'detail': detail, 'rows': len(rows)}


def sync_all(start: str = '', end: str = '') -> list[dict]:
    """Run all enabled connectors. Used by the scheduler at end-of-month."""
    if not start:
        start = date.today().replace(day=1).isoformat()
    if not end:
        end = date.today().isoformat()
    out = []
    for c in db.list_connectors(enabled_only=True):
        try:
            res = sync_one(c, start, end)
            out.append({'connector': c['name'], **res})
        except Exception as e:  # noqa: BLE001
            log.warning('connector %s sync failed: %s', c['name'], e)
            out.append({'connector': c['name'], 'ok': False, 'detail': str(e)})
    return out
