"""
Payroll computation + export.

Given a date range, builds a per-person row with:
  - present days
  - absent days (excluding holidays + approved leave)
  - late count
  - total worked hours (sum of check_out - check_in per day)
  - overtime hours (anything over `day_hours`)
  - amount payable (for site muster: hours * daily_rate / 8 + ot * 1.5)

Exports as CSV / XLSX from `app.py`.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Optional

import db


def date_range(start_iso: str, end_iso: str):
    a = date.fromisoformat(start_iso)
    b = date.fromisoformat(end_iso)
    n = (b - a).days + 1
    return [(a + timedelta(days=i)).isoformat() for i in range(n)]


def _hours_between(t_in: Optional[str], t_out: Optional[str]) -> float:
    if not t_in or not t_out:
        return 0.0
    try:
        fmt = '%H:%M:%S'
        a = datetime.strptime(t_in, fmt)
        b = datetime.strptime(t_out, fmt)
        return max(0.0, (b - a).total_seconds() / 3600.0)
    except Exception:  # noqa: BLE001
        return 0.0


def compute(start_iso: str, end_iso: str,
            branch_id: Optional[int] = None,
            day_hours: float = 8.0) -> list[dict]:
    """O(persons + attendance + leaves) instead of O(persons × days × attendance).

    Two SQL fetches, the rest happens in-memory:
      1. every attendance row in the window (one indexed range scan)
      2. every approved leave that overlaps the window
    """
    days = date_range(start_iso, end_iso)
    days_set = set(days)
    persons = db.list_persons()
    person_ids = [p['person_id'] for p in persons]
    if not person_ids:
        return []

    # 1) Holidays (small lookup)
    holidays_set = {h['date'] for h in db.list_holidays()
                    if h['branch_id'] in (None, branch_id)}
    working_days = [d for d in days if d not in holidays_set]
    working_set = set(working_days)

    # 2) ALL attendance in window, in one query
    with db.tx() as c:
        att_rows = c.execute(
            'SELECT person_id, date, check_in, check_out, is_late '
            'FROM attendance WHERE date BETWEEN ? AND ?',
            (start_iso, end_iso)).fetchall()
        leave_rows = c.execute(
            "SELECT person_id, start_date, end_date FROM leave_requests "
            "WHERE status = 'approved' "
            "  AND NOT (end_date < ? OR start_date > ?)",
            (start_iso, end_iso)).fetchall()

    # Bucket attendance per person
    by_person: dict[str, list] = {}
    for r in att_rows:
        by_person.setdefault(r['person_id'], []).append(r)

    # Build a quick "is this person on leave on day D" lookup
    leave_days_per_person: dict[str, set[str]] = {}
    for r in leave_rows:
        try:
            a = date.fromisoformat(r['start_date'])
            b = date.fromisoformat(r['end_date'])
        except Exception:  # noqa: BLE001
            continue
        cur = a
        s = leave_days_per_person.setdefault(r['person_id'], set())
        while cur <= b:
            iso = cur.isoformat()
            if iso in working_set:
                s.add(iso)
            cur = cur + timedelta(days=1)

    out = []
    for p in persons:
        pid = p['person_id']
        present = late = 0
        worked = ot_hours = 0.0
        seen_days: set[str] = set()
        for r in by_person.get(pid, []):
            if r['date'] in holidays_set:
                continue
            if r['date'] not in working_set:
                continue
            seen_days.add(r['date'])
            if not r['check_in']:
                continue
            present += 1
            if r['is_late']:
                late += 1
            h = _hours_between(r['check_in'], r['check_out'])
            worked += h
            if h > day_hours:
                ot_hours += h - day_hours
        on_leave_days = leave_days_per_person.get(pid, set()) - seen_days
        on_leave = len(on_leave_days)
        absent_real = max(0, len(working_days) - present - on_leave)
        out.append({
            'person_id': pid, 'name': p['name'],
            'department': p['department_name'],
            'present': present, 'late': late, 'absent': absent_real,
            'on_leave': on_leave,
            'worked_hours': round(worked, 2),
            'overtime_hours': round(ot_hours, 2),
        })
    return out


def site_wages(date_iso: str,
               branch_id: Optional[int] = None) -> list[dict]:
    """Per-worker wage summary for a single site day."""
    rows = db.site_muster_for(date_iso, branch_id=branch_id)
    out = []
    for r in rows:
        hourly = (r['daily_rate'] or 0) / 8.0
        amount = round(r['hours'] * hourly + r['overtime_hr'] * hourly * 1.5, 2)
        out.append({**r, 'amount': amount})
    return out
