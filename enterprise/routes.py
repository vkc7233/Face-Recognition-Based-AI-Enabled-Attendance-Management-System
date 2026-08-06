"""
Enterprise Edition routes (N1-N20).

A single Flask blueprint that wires every enterprise feature to a URL.
Mounted from app.py:

    from enterprise.routes import ent_bp
    app.register_blueprint(ent_bp)
"""

from __future__ import annotations

import io
import json
import logging
import secrets
import time
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlparse, urlencode, urljoin

from flask import (
    Blueprint, current_app, flash, jsonify, redirect, render_template,
    request, send_file, session, url_for,
)

import db
from enterprise import (
    analytics, chatbot, connectors as conn_mod, copilot, deepfake,
    presence as pres_mod, siem, sso, tailgating,
)

log = logging.getLogger(__name__)


ent_bp = Blueprint('enterprise', __name__, url_prefix='/enterprise')


# ---------------------------------------------------------------------------
def _admin_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if not session.get('admin'):
            return redirect(url_for('login', next=request.path))
        return fn(*a, **kw)
    return wrap


def _role(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            if not session.get('admin'):
                return redirect(url_for('login', next=request.path))
            r = session.get('role') or 'admin'
            if r == 'admin' or r in allowed:
                return fn(*a, **kw)
            flash('Insufficient role.', 'danger')
            return redirect(url_for('home'))
        return wrap
    return deco


# ===========================================================================
# N1 SSO — providers admin + auth flow
# ===========================================================================
@ent_bp.route('/sso')
@_role('admin')
def sso_admin():
    providers = db.list_sso_providers()
    for p in providers:
        p['_usable'] = sso.is_usable(p)
    return render_template('enterprise/sso.html',
                           providers=providers,
                           presets=list(sso.PRESETS.keys()))


@ent_bp.route('/sso', methods=['POST'])
@_role('admin')
def sso_add():
    if request.form.get('delete'):
        db.delete_sso_provider(int(request.form['delete']))
        return redirect(url_for('enterprise.sso_admin'))
    db.add_sso_provider(
        kind=request.form.get('kind', 'oidc'),
        name=request.form['name'],
        issuer=request.form.get('issuer'),
        client_id=request.form.get('client_id'),
        client_secret=request.form.get('client_secret'),
        auth_url=request.form.get('auth_url'),
        token_url=request.form.get('token_url'),
        userinfo_url=request.form.get('userinfo_url'),
        domain=request.form.get('domain'),
        default_role=request.form.get('default_role', 'staff'))
    flash('SSO provider saved.', 'success')
    return redirect(url_for('enterprise.sso_admin'))


@ent_bp.route('/sso/<int:provider_id>/login')
def sso_login(provider_id):
    p = db.get_sso_provider(provider_id)
    if not p or not p['enabled']:
        flash('Provider not available.', 'danger')
        return redirect(url_for('login'))
    state = secrets.token_urlsafe(24)
    verifier, challenge = sso.make_pkce()
    session['sso_state']    = state
    session['sso_verifier'] = verifier
    session['sso_provider'] = provider_id
    redirect_uri = url_for('enterprise.sso_callback', provider_id=provider_id,
                           _external=True)
    try:
        url = sso.build_authorize_url(p, redirect_uri, state, challenge)
    except Exception as e:  # noqa: BLE001
        flash(f'SSO misconfigured: {e}', 'danger')
        return redirect(url_for('login'))
    db.audit_ext('auth', 'sso_login_start', actor='-',
                 target=p['name'], ip=request.remote_addr,
                 user_agent=request.headers.get('User-Agent'))
    return redirect(url)


@ent_bp.route('/sso/<int:provider_id>/callback')
def sso_callback(provider_id):
    if request.args.get('state') != session.get('sso_state'):
        flash('SSO state mismatch.', 'danger')
        return redirect(url_for('login'))
    p = db.get_sso_provider(provider_id)
    if not p:
        return redirect(url_for('login'))
    code = request.args.get('code', '')
    verifier = session.pop('sso_verifier', '')
    redirect_uri = url_for('enterprise.sso_callback', provider_id=provider_id,
                           _external=True)
    try:
        tok = sso.exchange_code(p, code, redirect_uri, verifier)
        info = sso.fetch_userinfo(p, tok['access_token'])
    except Exception as e:  # noqa: BLE001
        log.warning('sso callback failed: %s', e)
        flash('SSO sign-in failed.', 'danger')
        return redirect(url_for('login'))
    email = sso.claim_email(info)
    subject = sso.claim_subject(info)
    name = sso.claim_name(info)
    if not sso.domain_allowed(p, email):
        flash('Your email domain is not allowed.', 'danger')
        return redirect(url_for('login'))
    username = db.upsert_admin_from_sso(provider_id, subject or email,
                                        email or '', name or email or 'user',
                                        role=p['default_role'] or 'staff')
    role = db.get_admin_role(username)
    session['admin'] = username
    session['role'] = role
    db.audit_ext('auth', 'sso_login_ok', actor=username,
                 target=p['name'], ip=request.remote_addr,
                 user_agent=request.headers.get('User-Agent'))
    flash('Signed in via SSO.', 'success')
    return redirect(url_for('home'))


# ===========================================================================
# N2 SCIM — admin only (clients live + tokens)
# ===========================================================================
@ent_bp.route('/scim', methods=['GET', 'POST'])
@_role('admin')
def scim_admin():
    new_token = None
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_scim_client(int(request.form['delete']))
        else:
            new_token, _ = db.add_scim_client(request.form['name'])
            flash('SCIM token created. Copy it now — it will not be shown again.',
                  'warning')
    return render_template('enterprise/scim.html',
                           clients=db.list_scim_clients(),
                           new_token=new_token)


# ===========================================================================
# N3 SIEM — sinks + run-once button
# ===========================================================================
@ent_bp.route('/siem', methods=['GET', 'POST'])
@_role('admin')
def siem_admin():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_siem_sink(int(request.form['delete']))
        elif request.form.get('drain'):
            siem.drain_once()
            flash('Drained pending audit rows.', 'success')
        else:
            db.add_siem_sink(
                request.form['name'], request.form['url'],
                request.form.get('auth_header', ''),
                request.form.get('fmt', 'json'))
        return redirect(url_for('enterprise.siem_admin'))
    return render_template('enterprise/siem.html',
                           sinks=db.list_siem_sinks(),
                           retention=siem.RETENTION_DAYS)


# ===========================================================================
# N4 SOC 2 / ISO 27001 evidence pack
# ===========================================================================
@ent_bp.route('/compliance')
@_admin_required
def compliance_pack():
    settings = db.get_all_settings()
    facts = {
        'encryption_at_rest':    settings.get('encrypt_templates') == '1',
        'data_residency':        'on-premise' if settings.get('camera_url', '').startswith(('rtsp:', 'http://192.', 'http://127.')) or not settings.get('camera_url') else 'unknown',
        'liveness':              settings.get('liveness_enabled', '1') == '1',
        'mfa_provider':          'SSO' if db.list_sso_providers(enabled_only=True) else 'none',
        'audit_extended_rows':   db.get_setting('audit_extended_rows') or 'live',
        'retention_days':        {
            'audit':         int(settings.get('retention_audit_days') or 365),
            'visitors':      int(settings.get('retention_visitors_days') or 30),
            'notifications': int(settings.get('retention_notifications_days') or 90),
            'gps':           int(settings.get('retention_gps_days') or 365),
        },
        'rbac_admins':           len(db.list_admins()),
        'tenants':               len(db.list_tenants()),
        'consents':              len(db.list_consents()),
        'backup_enabled':        settings.get('backup_enabled', '0') == '1',
        'sso_providers':         len(db.list_sso_providers(enabled_only=True)),
        'siem_sinks':            len(db.list_siem_sinks(enabled_only=True)),
    }
    return render_template('enterprise/compliance.html', facts=facts,
                           settings=settings)


@ent_bp.route('/compliance/export')
@_admin_required
def compliance_export():
    """Bundle the evidence as a single JSON the CISO can drop into a ticket."""
    settings = db.get_all_settings()
    pack = {
        'product': 'FaceMark',
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'controls': {
            'CC1 — Control Environment': 'Single tenant on-prem deployment with RBAC.',
            'CC2 — Communication & Information': 'Audit log + SIEM streaming.',
            'CC3 — Risk Assessment': 'Annual review + DPDP DPIA template included.',
            'CC4 — Monitoring Activities': 'Drift/at-risk dashboards, daily digests.',
            'CC5 — Control Activities': 'Approval workflows + 4-eye admin invites.',
            'CC6 — Logical Access': 'SSO/OIDC + MFA via IdP, no shared admin login.',
            'CC7 — System Operations': 'Encrypted backups (BYO destination).',
            'CC8 — Change Management': 'Versioned releases + audit on settings.',
            'CC9 — Risk Mitigation': 'Encrypted templates at rest, BYO-KMS.',
        },
        'iso_27001_annex_a': {
            'A.5 Information Security Policies': 'Provided in /docs.',
            'A.8 Asset Management':              'Persons + branches inventory in app.',
            'A.9 Access Control':                'RBAC (admin/hr/teacher/staff).',
            'A.10 Cryptography':                 'AES-256-GCM at rest; TLS 1.2+ in transit.',
            'A.12 Operations Security':          'SIEM streaming + retention policy.',
            'A.16 Incident Management':          'Audit log + SIEM alerting.',
            'A.18 Compliance':                   'DPDP/GDPR consent + right-to-erasure.',
        },
        'data_flows': [
            'Camera → FaceMark process (on-prem) → SQLite (on-prem) → SIEM (optional egress)',
            'Mobile (PWA) → /api/gps/check_in (TLS) → DB',
            'IdP (Okta/Entra/Google) → /enterprise/sso/<id>/callback → DB',
            'Outgoing notifications → SMTP/SMS/WhatsApp providers (egress)',
        ],
        'facts': compliance_pack_facts(),
    }
    buf = io.BytesIO(json.dumps(pack, indent=2, default=str).encode())
    return send_file(
        buf, as_attachment=True,
        download_name=f'FaceMark-Compliance-Pack-{date.today().isoformat()}.json',
        mimetype='application/json')


def compliance_pack_facts() -> dict:
    settings = db.get_all_settings()
    return {
        'encryption_at_rest': settings.get('encrypt_templates') == '1',
        'sso_enabled':        bool(db.list_sso_providers(enabled_only=True)),
        'siem_enabled':       bool(db.list_siem_sinks(enabled_only=True)),
        'audit_retention_days': int(settings.get('retention_audit_days') or 365),
    }


# ===========================================================================
# N5 Deepfake events
# ===========================================================================
@ent_bp.route('/spoof')
@_role('admin', 'hr')
def spoof_admin():
    return render_template('enterprise/spoof.html',
                           events=db.list_spoof_events())


# ===========================================================================
# N6 Tailgating / access events
# ===========================================================================
@ent_bp.route('/access')
@_role('admin', 'hr')
def access_admin():
    return render_template('enterprise/access.html',
                           events=db.list_access_events())


# ===========================================================================
# N7 BYO-KMS
# ===========================================================================
@ent_bp.route('/kms', methods=['GET', 'POST'])
@_role('admin')
def kms_admin():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_kms_key(int(request.form['delete']))
        else:
            db.add_kms_key(
                request.form['label'],
                request.form['kms_kind'],
                request.form['key_ref'])
        return redirect(url_for('enterprise.kms_admin'))
    return render_template('enterprise/kms.html',
                           keys=db.list_kms_keys())


# ===========================================================================
# N9 Teams / Slack endpoints
# ===========================================================================
@ent_bp.route('/slack/command', methods=['POST'])
def slack_command():
    raw = request.get_data() or b''
    if not chatbot.verify_slack_signature(
            dict(request.headers), raw,
            db.get_setting('slack_signing_secret') or ''):
        return jsonify({'text': 'invalid-signature'}), 401
    cmd = request.form.get('command', '')
    uid = request.form.get('user_id', '')
    ws  = request.form.get('team_id', '')
    text = request.form.get('text', '')
    res = chatbot.handle_command('slack', uid, cmd, workspace=ws, text=text)
    return jsonify(res)


@ent_bp.route('/teams/webhook', methods=['POST'])
def teams_webhook():
    j = request.get_json(force=True, silent=True) or {}
    text = (j.get('text') or '').strip()
    uid  = (j.get('from') or {}).get('id') or j.get('userId', '')
    parts = text.split()
    cmd = parts[0] if parts else 'help'
    res = chatbot.handle_command('teams', uid, '/' + cmd.lstrip('/'),
                                 workspace=j.get('tenantId', ''))
    return jsonify(chatbot.teams_response(res.get('text', '')))


# ===========================================================================
# N10 presence sync
# ===========================================================================
@ent_bp.route('/presence/wifi', methods=['POST'])
def presence_wifi():
    j = request.get_json(force=True, silent=True) or {}
    return jsonify(pres_mod.ingest_wifi(
        j.get('person_id', ''), j.get('ssid', ''), j.get('ap', '')))


@ent_bp.route('/presence/vpn', methods=['POST'])
def presence_vpn():
    j = request.get_json(force=True, silent=True) or {}
    return jsonify(pres_mod.ingest_vpn(
        j.get('person_id', ''), bool(j.get('active', True)),
        j.get('gateway', '')))


@ent_bp.route('/presence/calendar', methods=['POST'])
def presence_calendar():
    j = request.get_json(force=True, silent=True) or {}
    return jsonify(pres_mod.ingest_calendar(
        j.get('person_id', ''), j.get('location', ''), j.get('subject', '')))


# ===========================================================================
# N12 workflows
# ===========================================================================
@ent_bp.route('/workflows', methods=['GET', 'POST'])
@_role('admin', 'hr')
def workflows_admin():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_workflow(int(request.form['delete']))
        else:
            steps_raw = request.form.get('steps_json') or '[{"role":"hr"}]'
            db.add_workflow(request.form['name'],
                            request.form['trigger'],
                            steps_raw)
        return redirect(url_for('enterprise.workflows_admin'))
    return render_template('enterprise/workflows.html',
                           workflows=db.list_workflows(),
                           runs=db.list_workflow_runs())


@ent_bp.route('/workflow_runs/<int:run_id>/decide', methods=['POST'])
@_role('admin', 'hr')
def workflows_decide(run_id):
    decision = request.form.get('decision', 'approved')
    note = request.form.get('note', '')
    res = db.decide_workflow_step(run_id, session.get('admin', '-'),
                                   decision, note)
    flash(f"workflow → {res.get('state', '?')}", 'success' if res.get('ok') else 'danger')
    return redirect(url_for('enterprise.workflows_admin'))


# ===========================================================================
# N13 Copilot
# ===========================================================================
@ent_bp.route('/copilot', methods=['GET'])
@_role('admin', 'hr', 'teacher')
def copilot_page():
    return render_template('enterprise/copilot.html',
                           recent=db.recent_copilot(20))


@ent_bp.route('/copilot/ask', methods=['POST'])
@_admin_required
def copilot_ask():
    """Open to every logged-in role — staff using the portal can also ask
    "how do I check my history?" etc."""
    q = (request.form.get('q') or '').strip()
    if not q:
        return jsonify({'ok': False, 'summary': 'Type a question first.',
                        'rows': [], 'columns': []})
    res = copilot.answer(q, actor=session.get('admin', '-'))
    return jsonify(res)


# ===========================================================================
# N14 Workforce analytics
# ===========================================================================
@ent_bp.route('/analytics')
@_role('admin', 'hr')
def analytics_page():
    return render_template('enterprise/analytics.html',
                           attrition=analytics.attrition_scores(),
                           burnout=analytics.burnout_signals(),
                           trend=analytics.occupancy_trend())


# ===========================================================================
# N15 Connectors
# ===========================================================================
@ent_bp.route('/connectors', methods=['GET', 'POST'])
@_role('admin', 'hr')
def connectors_admin():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_connector(int(request.form['delete']))
        elif request.form.get('sync'):
            res = conn_mod.sync_all()
            flash(f'Synced {len(res)} connector(s).', 'success')
        else:
            db.add_connector(
                request.form['kind'], request.form['name'],
                request.form.get('endpoint', ''),
                request.form.get('api_key', ''),
                request.form.get('api_secret', ''))
        return redirect(url_for('enterprise.connectors_admin'))
    return render_template('enterprise/connectors.html',
                           connectors=db.list_connectors())


# ===========================================================================
# N16 Rooms + occupancy
# ===========================================================================
@ent_bp.route('/occupancy', methods=['GET', 'POST'])
@_role('admin', 'hr')
def occupancy_admin():
    if request.method == 'POST':
        db.add_room(request.form['name'],
                    int(request.form['branch_id']) if request.form.get('branch_id') else None,
                    int(request.form.get('capacity', '0')),
                    request.form.get('kind', 'meeting'))
        return redirect(url_for('enterprise.occupancy_admin'))
    return render_template('enterprise/occupancy.html',
                           rooms=db.list_rooms(),
                           branches=db.list_branches())


@ent_bp.route('/occupancy/<int:room_id>/log', methods=['POST'])
def occupancy_log(room_id):
    j = request.get_json(force=True, silent=True) or {}
    db.log_occupancy(room_id, int(j.get('head_count', 0)))
    return jsonify({'ok': True})


# ===========================================================================
# N8 Mustering
# ===========================================================================
@ent_bp.route('/muster', methods=['GET', 'POST'])
@_role('admin', 'hr')
def muster_page():
    if request.method == 'POST':
        if request.form.get('end'):
            db.end_muster(int(request.form['end']))
        else:
            db.start_muster(
                request.form['name'],
                int(request.form['branch_id']) if request.form.get('branch_id') else None,
                request.form.get('safe_zone', ''))
        return redirect(url_for('enterprise.muster_page'))
    active = db.active_muster()
    status = db.muster_status(active['id']) if active else None
    return render_template('enterprise/muster.html',
                           active=active,
                           status=status,
                           recent=db.list_musters(),
                           branches=db.list_branches())


@ent_bp.route('/muster/<int:drill_id>/checkin', methods=['POST'])
def muster_checkin(drill_id):
    j = request.get_json(force=True, silent=True) or {}
    pid = j.get('person_id')
    if not pid:
        return jsonify({'ok': False, 'error': 'person_id-required'}), 400
    ok = db.muster_check_in(drill_id, pid)
    return jsonify({'ok': True, 'newly_accounted': ok})


# ===========================================================================
# N17 Multi-tenant
# ===========================================================================
@ent_bp.route('/tenants', methods=['GET', 'POST'])
@_role('admin')
def tenants_admin():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_tenant(int(request.form['delete']))
        else:
            db.add_tenant(request.form['slug'], request.form['name'],
                          request.form.get('plan', 'starter'),
                          int(request.form['parent_id']) if request.form.get('parent_id') else None)
        return redirect(url_for('enterprise.tenants_admin'))
    return render_template('enterprise/tenants.html',
                           tenants=db.list_tenants())


# ===========================================================================
# N18 White-label
# ===========================================================================
@ent_bp.route('/whitelabel', methods=['GET', 'POST'])
@_role('admin')
def whitelabel_admin():
    if request.method == 'POST':
        tid = int(request.form['tenant_id'])
        db.update_tenant(tid,
                         brand_color=request.form.get('brand_color'),
                         brand_logo=request.form.get('brand_logo'),
                         name=request.form.get('name'))
        flash('Brand updated.', 'success')
        return redirect(url_for('enterprise.whitelabel_admin'))
    return render_template('enterprise/whitelabel.html',
                           tenants=db.list_tenants())


# ===========================================================================
# N19 SDK clients admin
# ===========================================================================
@ent_bp.route('/sdk', methods=['GET', 'POST'])
@_role('admin')
def sdk_admin():
    new_pair = None
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_sdk_client(int(request.form['delete']))
        else:
            pub, sec, _ = db.add_sdk_client(
                request.form['name'], request.form.get('origins', ''))
            new_pair = {'public_id': pub, 'secret': sec}
            flash('Copy the secret now — it will not be shown again.', 'warning')
    return render_template('enterprise/sdk.html',
                           clients=db.list_sdk_clients(),
                           new_pair=new_pair)


# ===========================================================================
# GTM pages — Pricing, Comparison, Playbook
# ===========================================================================
@ent_bp.route('/pricing')
@_admin_required
def pricing_page():
    return render_template('enterprise/pricing.html')


@ent_bp.route('/comparison')
@_admin_required
def comparison_page():
    return render_template('enterprise/comparison.html')


@ent_bp.route('/playbook')
@_admin_required
def playbook_page():
    return render_template('enterprise/playbook.html')


# ===========================================================================
# Enterprise hub (landing)
# ===========================================================================
@ent_bp.route('/')
@_admin_required
def enterprise_hub():
    return render_template(
        'enterprise/hub.html',
        sso=db.list_sso_providers(),
        scim_clients=db.list_scim_clients(),
        sinks=db.list_siem_sinks(),
        kms=db.list_kms_keys(),
        connectors=db.list_connectors(),
        spoof=db.list_spoof_events(5),
        access=db.list_access_events(5),
        tenants=db.list_tenants(),
        runs=db.list_workflow_runs(limit=5),
    )
