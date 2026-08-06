"""
N9 — Microsoft Teams and Slack check-in bot.

Two public webhooks:

  POST /enterprise/slack/command       — Slack slash command `/checkin`,
                                         `/checkout`, `/wfh`, `/status`
  POST /enterprise/teams/webhook       — Teams outgoing webhook with the
                                         same verbs in plain text

Both accept a free-text selfie URL (optional) — if attached, the recogniser
verifies it before accepting the event.

The mapping `chat_user_id → person_id` lives on persons.chat_user_id.
Admins set it from the user profile.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import logging

import db

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
def verify_slack_signature(headers: dict, raw_body: bytes,
                           signing_secret: str) -> bool:
    """Validate Slack's X-Slack-Signature so we know it's really Slack."""
    if not signing_secret:
        return True   # operator hasn't set the secret yet
    ts = headers.get('X-Slack-Request-Timestamp', '')
    sig = headers.get('X-Slack-Signature', '')
    if not ts or not sig:
        return False
    base = f'v0:{ts}:'.encode() + raw_body
    expect = 'v0=' + hmac.new(signing_secret.encode(), base,
                              hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, sig)


# ---------------------------------------------------------------------------
def handle_command(channel: str, user_id: str, command: str,
                   workspace: str = '', text: str = '') -> dict:
    """Process a /checkin /checkout /wfh /status command and return a chat
    response payload."""
    person = db.find_person_by_chat_user(user_id)
    if not person:
        return {
            'response_type': 'ephemeral',
            'text': ('Your chat account is not linked to a FaceMark person yet. '
                     'Ask your HR admin to set "Chat user ID" on your profile '
                     f'to `{user_id}`.')
        }

    pid = person['person_id']
    verb = command.lstrip('/').lower()

    if verb in ('checkin', 'check-in', 'in'):
        ws = db.get_setting('work_start_time', '09:00')
        lt = int(db.get_setting('late_threshold_min', '15'))
        res = db.mark_attendance(pid, ws, lt,
                                 int(db.get_setting('min_checkout_gap_min', '30')))
        db.log_chat_checkin(channel, user_id, 'check_in',
                            workspace=workspace, person_id=pid)
        return {
            'response_type': 'in_channel',
            'text': f"✅ {person['name']}: check-in at {res.get('time','')}"
                    + (' (late)' if res.get('is_late') else '')
        }

    if verb in ('checkout', 'check-out', 'out'):
        res = db.manual_check_out(pid)
        db.log_chat_checkin(channel, user_id, 'check_out',
                            workspace=workspace, person_id=pid)
        return {'response_type': 'in_channel',
                'text': f"👋 {person['name']}: check-out at {res.get('time','')}"}

    if verb in ('wfh', 'workfromhome', 'remote'):
        db.log_chat_checkin(channel, user_id, 'wfh',
                            workspace=workspace, person_id=pid)
        db.log_presence(pid, 'chat-wfh', detail=channel)
        return {'response_type': 'in_channel',
                'text': f"💻 {person['name']}: working from home today."}

    if verb in ('status', 'whoami'):
        rows = db.list_attendance()
        mine = next((r for r in rows if r['person_id'] == pid), None)
        if not mine:
            text = f"{person['name']}: not yet marked today."
        else:
            text = (f"{person['name']}: in {mine['check_in'] or '—'}, "
                    f"out {mine['check_out'] or '—'}, "
                    f"{'late' if mine['is_late'] else 'on time'}.")
        return {'response_type': 'ephemeral', 'text': text}

    return {'response_type': 'ephemeral',
            'text': 'Try `/checkin`, `/checkout`, `/wfh`, or `/status`.'}


# ---------------------------------------------------------------------------
def teams_response(text: str) -> dict:
    """Teams Outgoing-webhook expects a payload like this."""
    return {'type': 'message', 'text': text}
