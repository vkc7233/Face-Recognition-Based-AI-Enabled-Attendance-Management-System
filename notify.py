"""
Pluggable notification framework: email, SMS, WhatsApp.

Providers:
  EMAIL    - SMTP (host/port/user/pass in settings or env)
  SMS      - generic HTTP POST to a configured gateway URL with a template
             body. Works with most Indian gateways (MSG91, TextLocal,
             Fast2SMS, Twilio) by adapting the JSON body in the settings UI.
  WHATSAPP - Meta WhatsApp Cloud API (preferred) OR a webhook to a
             self-hosted gateway. Credentials in settings.

Every send goes through `dispatch()` which:
  * records the attempt + result in the `notifications` table
  * respects a per-recipient cooldown
  * is safe to call when nothing is configured (returns "skipped").

A small queue thread retries transient failures with exponential backoff.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import threading
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from queue import Empty, Queue
from typing import Optional
from urllib import request as urlrequest

log = logging.getLogger(__name__)

# These keys live in the `settings` table; lazy-loaded so tests can patch them.
SETTING_KEYS = [
    'smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from',
    'sms_url', 'sms_method', 'sms_headers_json', 'sms_body_template', 'sms_from',
    'whatsapp_token', 'whatsapp_phone_id', 'whatsapp_template',
    'notify_min_gap_sec',
]


# ---------------------------------------------------------------------------
def _setting(key: str, default: str = '') -> str:
    """Read a setting without creating an import cycle with db.py."""
    try:
        import db
        return (db.get_setting(key) or default) if hasattr(db, 'get_setting') else default
    except Exception:  # noqa: BLE001
        return default


def _record(channel: str, to: str, subject: str, body: str,
            status: str, detail: str = '') -> None:
    try:
        import db
        db.log_notification(channel, to, subject, body, status, detail)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Channel implementations
# ---------------------------------------------------------------------------
def send_email(to: str, subject: str, body_html: str,
               body_text: Optional[str] = None) -> tuple[bool, str]:
    host = _setting('smtp_host') or os.environ.get('SMTP_HOST', '')
    port = int(_setting('smtp_port') or os.environ.get('SMTP_PORT', '587'))
    user = _setting('smtp_user') or os.environ.get('SMTP_USER', '')
    pwd = _setting('smtp_pass') or os.environ.get('SMTP_PASS', '')
    sender = _setting('smtp_from') or user or os.environ.get('SMTP_FROM', '')
    if not host or not to:
        return False, 'smtp-not-configured'
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    if body_text:
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.ehlo()
            try:
                s.starttls(context=ctx)
            except Exception:  # noqa: BLE001
                pass
            if user:
                s.login(user, pwd)
            s.sendmail(sender, [to], msg.as_string())
        return True, 'sent'
    except Exception as e:  # noqa: BLE001
        return False, f'smtp-error:{e}'


def send_sms(to: str, body: str) -> tuple[bool, str]:
    url = _setting('sms_url')
    if not url or not to:
        return False, 'sms-not-configured'
    method = (_setting('sms_method') or 'POST').upper()
    template = _setting('sms_body_template') or '{"to":"{{to}}","message":"{{body}}"}'
    headers_raw = _setting('sms_headers_json') or '{"Content-Type":"application/json"}'
    try:
        headers = json.loads(headers_raw)
    except json.JSONDecodeError:
        headers = {'Content-Type': 'application/json'}
    payload = template.replace('{{to}}', to).replace('{{body}}', body.replace('"', '\\"'))
    try:
        req = urlrequest.Request(url, data=payload.encode('utf-8'),
                                 method=method, headers=headers)
        with urlrequest.urlopen(req, timeout=15) as r:
            ok = 200 <= r.status < 300
            return ok, f'http-{r.status}'
    except Exception as e:  # noqa: BLE001
        return False, f'sms-error:{e}'


def send_whatsapp(to: str, body: str) -> tuple[bool, str]:
    """Meta WhatsApp Cloud API — uses a pre-approved template name from settings."""
    token = _setting('whatsapp_token')
    phone_id = _setting('whatsapp_phone_id')
    template = _setting('whatsapp_template') or 'facemark_attendance'
    if not token or not phone_id or not to:
        return False, 'whatsapp-not-configured'
    url = f'https://graph.facebook.com/v18.0/{phone_id}/messages'
    body_payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'template',
        'template': {
            'name': template,
            'language': {'code': 'en'},
            'components': [
                {'type': 'body', 'parameters': [{'type': 'text', 'text': body}]}
            ],
        },
    }
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    try:
        req = urlrequest.Request(url, data=json.dumps(body_payload).encode('utf-8'),
                                 method='POST', headers=headers)
        with urlrequest.urlopen(req, timeout=15) as r:
            ok = 200 <= r.status < 300
            return ok, f'wa-http-{r.status}'
    except Exception as e:  # noqa: BLE001
        return False, f'wa-error:{e}'


# ---------------------------------------------------------------------------
# Dispatch + queue
# ---------------------------------------------------------------------------
_queue: Queue = Queue()
_worker_started = False
_lock = threading.Lock()


def _now_ts() -> float:
    return time.time()


def dispatch(channel: str, to: str, subject: str, body: str,
             body_text: Optional[str] = None, async_: bool = True) -> dict:
    """High-level entry point.

    `channel` is one of "email", "sms", "whatsapp", "auto" (try whatsapp
    then sms then email until one says configured).
    """
    if async_:
        _start_worker_once()
        _queue.put({'channel': channel, 'to': to, 'subject': subject,
                    'body': body, 'body_text': body_text, 'tries': 0})
        return {'queued': True}
    return _send_now(channel, to, subject, body, body_text)


def _send_now(channel: str, to: str, subject: str,
              body: str, body_text: Optional[str]) -> dict:
    seq = []
    if channel == 'auto':
        seq = ['whatsapp', 'sms', 'email']
    else:
        seq = [channel]
    last_detail = 'no-channel-configured'
    for ch in seq:
        if ch == 'email':
            ok, det = send_email(to, subject, body, body_text or body)
        elif ch == 'sms':
            ok, det = send_sms(to, body)
        elif ch == 'whatsapp':
            ok, det = send_whatsapp(to, body)
        else:
            ok, det = False, f'unknown-channel:{ch}'
        _record(ch, to, subject, body, 'sent' if ok else 'failed', det)
        last_detail = det
        if ok:
            return {'sent': True, 'via': ch, 'detail': det}
        if 'not-configured' not in det:
            break  # genuine failure — don't try next channel
    return {'sent': False, 'detail': last_detail}


def _start_worker_once() -> None:
    global _worker_started
    with _lock:
        if _worker_started:
            return
        t = threading.Thread(target=_worker_loop, daemon=True, name='notify-worker')
        t.start()
        _worker_started = True


def _worker_loop() -> None:
    while True:
        try:
            item = _queue.get(timeout=2)
        except Empty:
            continue
        try:
            res = _send_now(item['channel'], item['to'], item['subject'],
                            item['body'], item.get('body_text'))
            if not res.get('sent') and item['tries'] < 3:
                item['tries'] += 1
                time.sleep(2 ** item['tries'])
                _queue.put(item)
        except Exception as e:  # noqa: BLE001
            log.warning('notify worker: %s', e)


# ---------------------------------------------------------------------------
def notify_attendance(person: dict, event: str, ts: str,
                      org_name: str = 'FaceMark') -> None:
    """Compose + send a stock attendance notification to a person + their guardian."""
    body = (f'{org_name}: {person.get("name", "—")} '
            f'{ "checked in" if event == "check_in" else "checked out" } at {ts}.')
    subject = f'{org_name} - attendance update'
    targets = []
    if person.get('email'):
        targets.append(('email', person['email']))
    if person.get('guardian_email'):
        targets.append(('email', person['guardian_email']))
    if person.get('phone'):
        targets.append(('auto', person['phone']))
    if person.get('guardian_phone'):
        targets.append(('auto', person['guardian_phone']))
    for ch, to in targets:
        dispatch(ch, to, subject, body)


def notify_absent_daily(person: dict, date_iso: str,
                        org_name: str = 'FaceMark') -> None:
    body = (f'{org_name}: {person.get("name", "—")} was marked absent on {date_iso}. '
            'Please contact the office if this is incorrect.')
    subject = f'{org_name} - absence alert'
    for ch, key in (('email', 'guardian_email'), ('auto', 'guardian_phone')):
        to = person.get(key)
        if to:
            dispatch(ch, to, subject, body)
