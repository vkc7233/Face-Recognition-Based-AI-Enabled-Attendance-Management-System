"""
N14 — Workforce analytics + attrition / burnout risk.

Turns attendance into management decisions: occupancy trends, overtime creep,
absenteeism patterns, and an at-risk attrition score per person.

Attrition risk
--------------
Per person we look at the last 90 days and compute four signals:

  1.  Lateness trend slope          — is lateness increasing?
  2.  Worked-hours volatility       — chaotic schedule?
  3.  Recent late + absent ratio
  4.  Weekend/late-night activity   — burnout indicator

These get folded into a single 0..100 risk score, banded into
LOW / WATCH / HIGH so HR can intervene on the right cases.

Burnout risk
------------
Recent overtime hours / day above the day_hours setting, weighted by frequency.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import pstdev, mean
from typing import Optional

import db
from infra.cache import memoize


def _hours(t_in: Optional[str], t_out: Optional[str]) -> float:
    if not t_in or not t_out:
        return 0.0
    try:
        a = datetime.strptime(t_in, '%H:%M:%S')
        b = datetime.strptime(t_out, '%H:%M:%S')
        return max(0.0, (b - a).total_seconds() / 3600.0)
    except Exception:  # noqa: BLE001
        return 0.0


@memoize(ttl=120)
def attrition_scores(window_days: int = 90) -> list[dict]:
    """Single attendance scan + in-memory roll-up per person."""
    cutoff = (datetime.now() - timedelta(days=window_days)).date().isoformat()
    persons = db.list_persons()
    name_by_id = {p['person_id']: p['name'] for p in persons}
    bucket: dict[str, list] = {}
    with db.tx() as c:
        for r in c.execute(
            'SELECT person_id, date, check_in, check_out, is_late '
            'FROM attendance WHERE date >= ? ORDER BY person_id, date',
            (cutoff,)).fetchall():
            bucket.setdefault(r['person_id'], []).append(r)

    out = []
    for pid, rows in bucket.items():
        n = len(rows)
        if n < 14:
            continue
        late_days = sum(1 for r in rows if r['is_late'])
        absent_days = max(0, window_days - n)
        present_days = sum(1 for r in rows if r['check_in'])
        hours = [_hours(r['check_in'], r['check_out']) for r in rows]
        hours_volatility = pstdev(hours) if len(hours) >= 2 else 0
        avg_hours = mean(hours) if hours else 0
        half = n // 2
        late_first = sum(1 for r in rows[:half] if r['is_late']) / max(1, half)
        late_second = sum(1 for r in rows[half:] if r['is_late']) / max(1, n - half)
        trend = max(0.0, late_second - late_first)
        score = (
            30 * min(1.0, trend * 4)
            + 25 * min(1.0, hours_volatility / 3.0)
            + 25 * (late_days / max(1, n))
            + 20 * (absent_days / max(1, window_days))
        )
        band = 'low'
        if score > 65: band = 'high'
        elif score > 40: band = 'watch'
        out.append({
            'person_id': pid,
            'name': name_by_id.get(pid, pid),
            'score': round(score, 1), 'band': band,
            'late_days': late_days, 'absent_days': absent_days,
            'present_days': present_days,
            'avg_hours': round(avg_hours, 2),
            'volatility': round(hours_volatility, 2),
            'trend': round(trend, 3),
        })
    out.sort(key=lambda x: -x['score'])
    return out


@memoize(ttl=120)
def burnout_signals(window_days: int = 30,
                    day_hours: float = 8.0) -> list[dict]:
    """Single scan + in-memory aggregation."""
    cutoff = (datetime.now() - timedelta(days=window_days)).date().isoformat()
    persons = db.list_persons()
    name_by_id = {p['person_id']: p['name'] for p in persons}
    by_pid: dict[str, tuple[float, int]] = {}
    with db.tx() as c:
        rows = c.execute(
            'SELECT person_id, check_in, check_out FROM attendance '
            'WHERE date >= ? AND check_in IS NOT NULL AND check_out IS NOT NULL',
            (cutoff,)).fetchall()
    for r in rows:
        h = _hours(r['check_in'], r['check_out'])
        if h <= day_hours:
            continue
        ot, days = by_pid.get(r['person_id'], (0.0, 0))
        by_pid[r['person_id']] = (ot + h - day_hours, days + 1)

    out = [
        {'person_id': pid, 'name': name_by_id.get(pid, pid),
         'overtime_hours': round(ot, 2),
         'overtime_days':  days,
         'avg_per_day':    round(ot / days, 2)}
        for pid, (ot, days) in by_pid.items() if days >= 5
    ]
    out.sort(key=lambda x: -x['overtime_hours'])
    return out


def occupancy_trend(days: int = 14) -> list[dict]:
    """Daily presence headcount across the org."""
    with db.tx() as c:
        rows = c.execute(
            "SELECT date, COUNT(*) AS present "
            "FROM attendance WHERE date >= date('now', ?) "
            "AND check_in IS NOT NULL "
            "GROUP BY date ORDER BY date",
            (f'-{days - 1} days',)).fetchall()
    return [dict(r) for r in rows]
