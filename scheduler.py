"""
Background scheduler — no external dependency.

Runs a single daemon thread that wakes every 60 s and triggers:
  * daily 00:05  -> purge old data; auto-create timetable sessions for today
  * daily 18:30  -> daily digest emails (per branch HR / admin)
  * weekly Sun   -> at-risk persons summary
  * every 6 h    -> absent-alerts for guardians

Tasks are idempotent and use a `last_run` setting key per job so a process
restart never re-runs a job that already ran today.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from datetime import date, datetime

import db
import notify
import payroll
import cloud_backup

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now()


def _ran_today(key: str) -> bool:
    return (db.get_setting(f'cron_{key}') or '') == date.today().isoformat()


def _mark_ran(key: str) -> None:
    db.set_setting(f'cron_{key}', date.today().isoformat())


# ---------------------------------------------------------------------------
def _daily_purge() -> None:
    vd = int(db.get_setting('retention_visitors_days') or '30')
    nd = int(db.get_setting('retention_notifications_days') or '90')
    ad = int(db.get_setting('retention_audit_days') or '365')
    gd = int(db.get_setting('retention_gps_days') or '365')
    res = db.purge_old_data(vd, nd, ad, gd)
    log.info('retention purge: %s', res)


def _daily_timetable() -> None:
    today = date.today().isoformat()
    n = db.materialise_today_sessions(today)
    if n:
        log.info('materialised %s sessions from timetable for %s', n, today)


def _daily_digest() -> None:
    org = db.get_setting('org_name') or 'FaceMark'
    rows = db.list_attendance()
    present = sum(1 for r in rows if r['check_in'])
    late = sum(1 for r in rows if r['is_late'])
    total = db.count_persons()
    body = (f'<h3>{org} - daily attendance digest</h3>'
            f'<p>Date: <b>{date.today().isoformat()}</b></p>'
            f'<ul><li>Registered: {total}</li>'
            f'<li>Present: {present}</li>'
            f'<li>Late: {late}</li>'
            f'<li>Absent (registered, no check-in): {max(0, total - present)}</li>'
            f'</ul>')
    text = (f'{org} digest {date.today().isoformat()}: '
            f'{present} present, {late} late, of {total}.')
    recipients = (db.get_setting('digest_recipients') or '').split(',')
    for r in [x.strip() for x in recipients if x.strip()]:
        notify.dispatch('email', r, f'{org} - daily digest', body, text)


def _absent_alerts() -> None:
    today = date.today().isoformat()
    rows = db.yet_to_arrive()
    for r in rows:
        person = db.get_person(r['person_id'])
        if person:
            notify.notify_absent_daily(dict(person), today,
                                       db.get_setting('org_name') or 'FaceMark')


def _weekly_at_risk() -> None:
    org = db.get_setting('org_name') or 'FaceMark'
    risks = db.at_risk_persons()
    if not risks:
        return
    rows = ''.join(
        f'<tr><td>{r["name"]}</td><td>{r["recent_pct"]}%</td>'
        f'<td>{r["prior_pct"]}%</td><td>-{r["drop_pct"]}%</td></tr>'
        for r in risks[:25])
    body = (f'<h3>{org} - at-risk this week</h3>'
            f'<table border="1" cellpadding="6">'
            f'<tr><th>Name</th><th>Recent 14d</th><th>Prior 14d</th><th>Drop</th></tr>'
            f'{rows}</table>')
    recipients = (db.get_setting('digest_recipients') or '').split(',')
    for r in [x.strip() for x in recipients if x.strip()]:
        notify.dispatch('email', r, f'{org} - at-risk weekly', body, body)


# ---------------------------------------------------------------------------
_started = False
_lock = threading.Lock()


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        threading.Thread(target=_loop, daemon=True, name='facemark-cron').start()
        _started = True


def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception as e:  # noqa: BLE001
            log.warning('cron tick: %s', e)
        time.sleep(60)


def _tick() -> None:
    now = _now()
    hm = now.strftime('%H:%M')

    if hm >= '00:05' and not _ran_today('purge'):
        _daily_purge(); _mark_ran('purge')
    if hm >= '00:10' and not _ran_today('timetable'):
        _daily_timetable(); _mark_ran('timetable')
    if hm >= '18:30' and not _ran_today('digest'):
        _daily_digest(); _mark_ran('digest')
    if hm >= '11:00' and not _ran_today('absent_alerts'):
        _absent_alerts(); _mark_ran('absent_alerts')
    if now.weekday() == 6 and hm >= '20:00' and not _ran_today('weekly_atrisk'):
        _weekly_at_risk(); _mark_ran('weekly_atrisk')
    if hm >= '02:30' and not _ran_today('backup') \
            and (db.get_setting('backup_enabled') or '0') == '1':
        try:
            cloud_backup.run_backup_once()
        except Exception as e:  # noqa: BLE001
            log.warning('nightly backup failed: %s', e)
        _mark_ran('backup')
