"""
FaceMark — Face-Recognition Attendance System (commercial-grade build).

Highlights
----------
- LBPH recogniser (OpenCV contrib) with KNN fallback — see recognizer.py
- Face alignment, augmentation, blur rejection — see face_utils.py
- Multi-face per-frame recognition with voting buffer for stability
- Profile thumbnails per user
- Check-in / check-out flow with late-arrival flagging
- Manual mark / manual check-out / user edit
- Live recognition feed, yet-to-arrive panel
- Departments, bulk CSV import, Excel + CSV export
- Auth, settings, analytics, audit log
"""

from __future__ import annotations

import os
import re
import io
import csv
import logging
import time
from collections import defaultdict, deque
from datetime import date, datetime
from functools import wraps

# Robust RTSP behaviour for OpenCV's FFmpeg backend (must be set before cv2 import):
# force TCP transport (avoids UDP packet loss on a busy LAN) and put a 3s open timeout.
os.environ.setdefault(
    'OPENCV_FFMPEG_CAPTURE_OPTIONS',
    'rtsp_transport;tcp|stimeout;3000000|max_delay;500000',
)

import cv2
import numpy as np
import pandas as pd
from flask import (
    Flask, request, render_template, redirect, url_for,
    Response, send_file, flash, jsonify, session,
)

import db
import face_utils
import recognizer
import liveness
import geo
import notify
import i18n
import payroll
import ppe
import safety
import exam_mode
import cloud_backup
from restapi import api_bp, dispatch_event
import scheduler
import crypto_store

# Enterprise Edition (N1-N20)
from enterprise.routes import ent_bp
from enterprise.scim import scim_bp
from enterprise.sdk import sdk_bp
from enterprise import siem as ent_siem

# Infrastructure (perf + security + observability)
from infra import middleware as infra_mw
from infra import csrf as infra_csrf
from infra.health import health_bp, record as infra_record_request
from infra.ratelimit import limit as rl
from cctv_wall import cctv_bp

# ---------------------------------------------------------------------------
NIMGS = 25
FACES_DIR = os.path.join('static', 'faces')
PROFILE_DIR = os.path.join('static', 'profiles')
VISITOR_DIR = os.path.join('static', 'visitors')
PPE_DIR = os.path.join('static', 'ppe')
VISITOR_COOLDOWN_SEC = 30        # max one snapshot per face slot in this window

app = Flask(__name__)
app.secret_key = os.environ.get('FACEMARK_SECRET', 'change-me-in-production')

# Session hardening
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('FACEMARK_FORCE_HTTPS') == '1',
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,  # 12 h
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,       # 20 MB upload ceiling
    JSON_SORT_KEYS=False,
    SEND_FILE_MAX_AGE_DEFAULT=60 * 60 * 24 * 30,  # 30-day static cache
)

# Infrastructure first so every request gets headers + ids
infra_mw.install(app)
infra_csrf.install(app)
app.register_blueprint(health_bp)

# Feature blueprints
app.register_blueprint(api_bp)
app.register_blueprint(ent_bp)
app.register_blueprint(scim_bp)
app.register_blueprint(sdk_bp)


# Tap into the access log + Prometheus histogram
@app.after_request
def _metrics_record(resp):
    try:
        ms = (time.perf_counter() - getattr(__import__('flask').g, 't0', time.perf_counter())) * 1000.0
        infra_record_request(resp.status_code, ms)
    except Exception:  # noqa: BLE001
        pass
    return resp


# Expose CSP nonce + helpful globals to every template
@app.context_processor
def _inject_infra():
    return {'csp_nonce': infra_mw.csp_nonce,
            'asset_version': os.environ.get('FACEMARK_ASSET_VERSION', 'v2')}


# Pretty error pages — also expose request_id so SOC can correlate to SIEM
@app.errorhandler(400)
def _err_400(e):
    from flask import g as _g
    return render_template('error.html', code=400,
                           title='Bad request',
                           detail=getattr(e, 'description', 'The request was rejected.'),
                           request_id=getattr(_g, 'request_id', '-'),
                           org_name=db.get_setting('org_name')), 400


@app.errorhandler(404)
def _err_404(e):
    from flask import g as _g
    return render_template('error.html', code=404,
                           title='Page not found',
                           detail="That page doesn't exist on this server.",
                           request_id=getattr(_g, 'request_id', '-'),
                           org_name=db.get_setting('org_name')), 404


@app.errorhandler(429)
def _err_429(e):
    from flask import g as _g
    return render_template('error.html', code=429,
                           title='Too many requests',
                           detail='Please slow down — you are being rate-limited.',
                           request_id=getattr(_g, 'request_id', '-'),
                           org_name=db.get_setting('org_name')), 429


@app.errorhandler(500)
def _err_500(e):
    from flask import g as _g
    log.exception('500 on %s — %s', request.path, e)
    return render_template('error.html', code=500,
                           title='Something went wrong',
                           detail='The team has been notified. Try again in a moment.',
                           request_id=getattr(_g, 'request_id', '-'),
                           org_name=db.get_setting('org_name')), 500


# Long-lived caching for /static assets (immutable filenames via ?v=)
@app.after_request
def _cache_static(resp):
    if request.path.startswith('/static/'):
        if 'v=' in request.query_string.decode('utf-8', errors='ignore'):
            resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        else:
            resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp
app.register_blueprint(cctv_bp)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# Streaming state
_capture_mode = {'mode': None, 'username': None, 'userid': None,
                 'count': 0, 'frame_idx': 0, 'sharp_total': 0.0}

# Identity-based voting buffer for crowd mode.
# key   = predicted label (e.g. "Aisha_1042")
# value = deque of (timestamp_seconds, confidence) — only entries within the
#         CROWD_VOTE_WINDOW are considered when deciding to mark.
CROWD_VOTE_WINDOW = 2.5          # seconds of history considered
CROWD_VOTES_NEEDED = 3           # this many hits in the window = confirmed
CROWD_MARK_COOLDOWN = 8.0        # don't re-mark same person within this many seconds
CAPTURE_FLASH_SECONDS = 1.5      # show "Captured" badge for this long
_visitor_cooldown = 0.0          # last visitor-snapshot timestamp

_vote_buffer: dict[str, deque] = defaultdict(lambda: deque(maxlen=15))
_recent_marks: dict[str, float] = {}            # person_id -> last mark ts
_just_captured: dict[str, dict] = {}            # person_id -> {expires, name, last_bbox}


# ---------------------------------------------------------------------------
def ensure_dirs():
    """Create the media directories, tolerating dangling symlinks.

    The Docker image points these at the /data volume (static/faces ->
    /data/static_faces) but only creates /data itself, so on a fresh container
    they are symlinks to targets that do not exist yet. os.makedirs(exist_ok=True)
    raises FileExistsError on those: mkdir() fails with EEXIST because the link
    occupies the name, and the exist_ok guard then calls os.path.isdir(), which
    follows the link to nothing and returns False, so the error is re-raised.
    Creating os.path.realpath(d) instead creates the *target*, which makes the
    link resolve. realpath() is a no-op for a plain directory.
    """
    for d in (FACES_DIR, PROFILE_DIR, VISITOR_DIR, PPE_DIR):
        os.makedirs(os.path.realpath(d), exist_ok=True)


def total_registered() -> int:
    if not os.path.isdir(FACES_DIR):
        return 0
    return sum(1 for d in os.listdir(FACES_DIR)
               if os.path.isdir(os.path.join(FACES_DIR, d)) and os.listdir(os.path.join(FACES_DIR, d)))


def split_user_folder(folder_name):
    parts = folder_name.rsplit('_', 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (folder_name, '')


# ---------------------------------------------------------------------------
def login_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if not session.get('admin'):
            if request.path.startswith(('/api/', '/video_feed', '/start_', '/stop_', '/capture_status')):
                return jsonify({'ok': False, 'msg': 'auth required'}), 401
            return redirect(url_for('login', next=request.path))
        return fn(*a, **kw)
    return wrap


# RBAC role hierarchy. Higher index = more privileged.
ROLE_LEVELS = ['staff', 'teacher', 'hr', 'admin']


def role_required(*allowed):
    """Decorator: only allow if session role is in `allowed` (or admin)."""
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            if not session.get('admin'):
                return redirect(url_for('login', next=request.path))
            role = session.get('role') or 'admin'
            if role == 'admin' or role in allowed:
                return fn(*a, **kw)
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'forbidden'}), 403
            flash(f'Requires one of: {", ".join(allowed)}', 'danger')
            return redirect(url_for('home'))
        return wrap
    return deco


def portal_required(fn):
    """For self-service / parent portal — separate session key."""
    @wraps(fn)
    def wrap(*a, **kw):
        if not session.get('portal_pid'):
            return redirect(url_for('portal_login', next=request.path))
        return fn(*a, **kw)
    return wrap


@app.route('/login', methods=['GET', 'POST'])
@rl('10/m', key='login')
def login():
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''
        row = db.verify_admin(u, p)
        if row:
            session['admin'] = u
            session['role'] = row['role'] if hasattr(row, 'keys') and 'role' in row.keys() else 'admin'
            db.audit(u, 'login', '')
            return redirect(request.args.get('next') or url_for('home'))
        flash('Invalid credentials.', 'danger')
    from enterprise.sso import list_usable as _sso_usable
    providers = db.list_sso_providers(enabled_only=True)
    return render_template('login.html',
                           org_name=db.get_setting('org_name'),
                           sso_providers=_sso_usable(providers))


@app.route('/logout')
def logout():
    u = session.get('admin')
    session.clear()
    if u:
        db.audit(u, 'logout', '')
    return redirect(url_for('login'))


@app.context_processor
def inject_globals():
    active_id = (db.get_setting('active_session_id') or '').strip()
    active_session = None
    if active_id.isdigit():
        active_session = db.get_session(int(active_id))
    lang = session.get('lang') or i18n.detect(request)
    return {
        'org_name': db.get_setting('org_name'),
        'admin_user': session.get('admin'),
        'admin_role': session.get('role') or 'admin',
        'datetoday2': date.today().strftime('%d %B %Y'),
        'model_trained': recognizer.get().is_trained(),
        'recognizer_backend': recognizer.get().name.upper(),
        'active_session': active_session,
        'lang': lang,
        'T': lambda k, l=lang: i18n.T(k, l),
        'languages': i18n.languages(),
        'liveness_on': (db.get_setting('liveness_enabled') or '1') == '1',
        'site_mode_on': (db.get_setting('site_mode_enabled') or '0') == '1',
    }


@app.route('/lang/<code>')
def set_lang(code):
    if code in i18n.SUPPORTED:
        session['lang'] = code
    return redirect(request.referrer or url_for('home'))


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
def open_camera():
    """Open the default webcam OR a configured RTSP / HTTP IP camera URL.
    Set the `camera_url` setting to e.g. rtsp://user:pass@cam.local/Streaming/Channels/101
    to use an IP camera instead of a USB webcam.
    """
    cam_url = (db.get_setting('camera_url') or '').strip()
    if cam_url:
        cap = cv2.VideoCapture(cam_url)
    else:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) if os.name == 'nt' else cv2.VideoCapture(0)
    if not cap.isOpened():
        return None
    if not cam_url:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap


def _draw_label(frame, x, y, w, h, text, color):
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
    cv2.rectangle(frame, (x, y - th - 12), (x + max(w, tw + 10), y), color, -1)
    cv2.putText(frame, text, (x + 5, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)


def gen_frames():
    cap = open_camera()
    if cap is None:
        err = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(err, 'Cannot open webcam', (60, 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        _, buf = cv2.imencode('.jpg', err)
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
        return

    try:
        threshold = float(db.get_setting('recognition_threshold', '80'))
        work_start = db.get_setting('work_start_time', '09:00')
        late_min = int(db.get_setting('late_threshold_min', '15'))
        gap_min = int(db.get_setting('min_checkout_gap_min', '30'))
        rec = recognizer.get()

        while True:
            ok, frame = cap.read()
            if not ok:
                break

            mode = _capture_mode['mode']
            faces = face_utils.detect_faces(frame)

            if mode == 'recognise' and rec.is_trained():
                now_ts = time.time()
                recognized_count = 0

                # 1) Predict every face and grow the per-identity vote buffer
                per_face = []   # list of dicts: { bbox, label, conf }
                for (x, y, w, h) in faces:
                    crop = frame[y:y + h, x:x + w]
                    gray = face_utils.preprocess(crop)
                    label, conf = rec.predict(gray)
                    accepted = label is not None and conf <= threshold
                    if accepted:
                        _vote_buffer[label].append((now_ts, conf))
                    per_face.append({'bbox': (x, y, w, h),
                                     'label': label if accepted else None,
                                     'conf': conf})

                # 2) Prune stale votes
                cutoff = now_ts - CROWD_VOTE_WINDOW
                for k in list(_vote_buffer.keys()):
                    while _vote_buffer[k] and _vote_buffer[k][0][0] < cutoff:
                        _vote_buffer[k].popleft()
                    if not _vote_buffer[k]:
                        del _vote_buffer[k]

                # 3) For each face, decide its visual state
                for f in per_face:
                    x, y, w, h = f['bbox']
                    label = f['label']
                    conf = f['conf']

                    if label and len(_vote_buffer.get(label, [])) >= CROWD_VOTES_NEEDED:
                        # Confirmed identity
                        _, pid = split_user_folder(label)
                        last = _recent_marks.get(pid, 0)
                        person = db.get_person(pid)
                        nm = person['name'] if person else label.rsplit('_', 1)[0]

                        # ── LIVENESS gate ────────────────────────────────
                        liveness_on = (db.get_setting('liveness_enabled') or '1') == '1'
                        live_state = liveness.tracker().update(
                            liveness.make_face_id(label, (x, y, w, h)),
                            (x, y, w, h), frame[y:y + h, x:x + w])
                        live_ok = (not liveness_on) or live_state['live']

                        # ── MASK detection (advisory) ────────────────────
                        mask_required = (db.get_setting('mask_required') or '0') == '1'
                        if mask_required:
                            m_state = safety.detect_mask(frame[y:y + h, x:x + w])
                            if not m_state['mask']:
                                _draw_label(frame, x, y, w, h,
                                            f'Mask required (cov {m_state["coverage"]:.2f})',
                                            (220, 200, 60))
                                continue

                        # ── PPE gate (site mode only) ────────────────────
                        site_mode = (db.get_setting('site_mode_enabled') or '0') == '1'
                        ppe_required = (db.get_setting('ppe_required') or 'helmet,vest').split(',')
                        ppe_state = None
                        if site_mode:
                            ppe_state = ppe.detect(frame, (x, y, w, h))
                            missing = []
                            if 'helmet' in ppe_required and not ppe_state['helmet']:
                                missing.append('helmet')
                            if 'vest' in ppe_required and not ppe_state['vest']:
                                missing.append('vest')
                            if missing:
                                # log + visually flag, don't mark attendance
                                snap_name = f'ppe_{pid}_{int(now_ts)}.jpg'
                                try:
                                    cv2.imwrite(os.path.join(PPE_DIR, snap_name),
                                                cv2.resize(frame[max(0, y-20):y+h+80,
                                                                 max(0, x-30):x+w+30], (300, 360)))
                                    db.log_ppe_incident(
                                        pid, person['branch_id'] if person else None,
                                        ','.join([k for k in ('helmet', 'vest')
                                                  if ppe_state.get(k)]),
                                        ','.join(missing),
                                        f'ppe/{snap_name}')
                                except Exception as e:  # noqa: BLE001
                                    log.warning('ppe snap failed: %s', e)
                                _draw_label(frame, x, y, w, h,
                                            f'PPE missing: {" + ".join(missing)}',
                                            (39, 39, 220))
                                continue

                        if now_ts - last > CROWD_MARK_COOLDOWN and live_ok:
                            try:
                                res = db.mark_attendance(pid, work_start, late_min, gap_min)
                                # Active class session? mark there too
                                active_sid = (db.get_setting('active_session_id') or '').strip()
                                if active_sid.isdigit():
                                    db.mark_session_attendance(int(active_sid), pid)
                                # Notifications + webhook
                                if person:
                                    notify.notify_attendance(
                                        dict(person), res.get('event', 'check_in'),
                                        res.get('time', ''),
                                        db.get_setting('org_name') or 'FaceMark')
                                dispatch_event(res.get('event', 'check_in'),
                                               {'person_id': pid, **res})
                                # Door relay (if configured)
                                if (db.get_setting('door_relay_url') or '').strip():
                                    safety.trigger_door(
                                        person['branch_id'] if person else None, pid)
                            except Exception as e:  # noqa: BLE001
                                log.warning('mark_attendance failed: %s', e)
                            _recent_marks[pid] = now_ts
                            _just_captured[pid] = {
                                'expires': now_ts + CAPTURE_FLASH_SECONDS,
                                'name': nm,
                                'bbox': (x, y, w, h),
                            }
                        elif liveness_on and not live_ok:
                            # Liveness still pending — draw amber "blink please"
                            tags = []
                            if not live_state['blink_ok']:  tags.append('blink')
                            if not live_state['motion_ok']: tags.append('motion')
                            if not live_state['texture_ok']: tags.append('texture')
                            _draw_label(frame, x, y, w, h,
                                        f'Liveness: {"+".join(tags)}',
                                        (39, 174, 240))
                            continue

                        # Capture-flash overlay if we marked them recently
                        flashing = (pid in _just_captured and
                                    _just_captured[pid]['expires'] > now_ts)
                        color = (46, 204, 113) if not flashing else (39, 174, 96)
                        _draw_label(frame, x, y, w, h, f'{nm}  ({conf:.0f})', color)
                        if flashing:
                            # green corner check + "CAPTURED" tag
                            cv2.putText(frame, '✓ CAPTURED', (x, y + h + 22),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                        (39, 174, 96), 2)
                            cv2.circle(frame, (x + w - 14, y + 14), 9,
                                       (39, 174, 96), -1)
                            cv2.putText(frame, 'OK', (x + w - 22, y + 18),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                                        (255, 255, 255), 1)
                        recognized_count += 1

                    elif label:
                        # Recognised but not yet enough votes
                        votes_have = len(_vote_buffer.get(label, []))
                        _draw_label(frame, x, y, w, h,
                                    f'Verifying {votes_have}/{CROWD_VOTES_NEEDED}',
                                    (241, 196, 15))
                    else:
                        _draw_label(frame, x, y, w, h, 'Unknown', (148, 148, 148))
                        # Log unknown face (once per VISITOR_COOLDOWN_SEC) for security
                        global _visitor_cooldown
                        if now_ts - _visitor_cooldown > VISITOR_COOLDOWN_SEC:
                            try:
                                fname = f'visitor_{int(now_ts)}.jpg'
                                cv2.imwrite(os.path.join(VISITOR_DIR, fname),
                                            cv2.resize(frame[y:y + h, x:x + w], (200, 200)))
                                db.log_visitor(f'visitors/{fname}', camera='Main')
                                _visitor_cooldown = now_ts
                            except Exception as e:  # noqa: BLE001
                                log.warning('visitor snap failed: %s', e)

                # 4) Clear stale capture flashes
                for pid in list(_just_captured.keys()):
                    if _just_captured[pid]['expires'] < now_ts:
                        del _just_captured[pid]

                # 5) Exam continuous-presence (if an exam is active)
                exam = db.get_active_exam()
                if exam:
                    recognised_pids = [f['label'].rsplit('_', 1)[1]
                                       for f in per_face
                                       if f['label'] and '_' in f['label']]
                    unknown = [f['bbox'] for f in per_face if not f['label']]
                    alerts = exam_mode.observe(exam, recognised_pids, unknown)
                    if alerts:
                        cv2.putText(frame, f'EXAM ALERTS: {len(alerts)}',
                                    (15, frame.shape[0] - 18),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                                    (40, 40, 255), 2)

                # 6) HUD top-right
                hud = f'Faces: {len(faces)}  Recognized: {recognized_count}'
                (tw, th), _b = cv2.getTextSize(hud, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                fw = frame.shape[1]
                cv2.rectangle(frame, (fw - tw - 24, 12),
                              (fw - 8, 12 + th + 14), (0, 0, 0), -1)
                cv2.putText(frame, hud, (fw - tw - 16, 12 + th + 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            elif mode == 'register':
                _capture_mode['frame_idx'] += 1
                for (x, y, w, h) in faces:
                    crop = frame[y:y + h, x:x + w]
                    sharp = face_utils.quality_score(crop)
                    too_blurry = sharp < face_utils.BLUR_THRESHOLD
                    color = (52, 152, 219) if not too_blurry else (0, 100, 200)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

                    cv2.putText(frame, f'Sharpness {sharp:.0f}',
                                (x, y + h + 22), cv2.FONT_HERSHEY_SIMPLEX,
                                0.55, color, 2)

                    if (not too_blurry and
                            _capture_mode['frame_idx'] % 4 == 0 and
                            _capture_mode['count'] < NIMGS):
                        folder = os.path.join(
                            FACES_DIR,
                            f"{_capture_mode['username']}_{_capture_mode['userid']}"
                        )
                        os.makedirs(folder, exist_ok=True)
                        fname = f"{_capture_mode['username']}_{_capture_mode['count']}.jpg"
                        cv2.imwrite(os.path.join(folder, fname), crop)

                        # save the first sharp crop as the profile thumbnail
                        if _capture_mode['count'] == 0:
                            os.makedirs(PROFILE_DIR, exist_ok=True)
                            cv2.imwrite(
                                os.path.join(PROFILE_DIR, f"{_capture_mode['userid']}.jpg"),
                                cv2.resize(crop, (160, 160))
                            )

                        _capture_mode['count'] += 1
                        _capture_mode['sharp_total'] += sharp

                pct = int(100 * _capture_mode['count'] / NIMGS)
                cv2.rectangle(frame, (15, 15), (15 + 250, 38), (0, 0, 0), -1)
                cv2.rectangle(frame, (15, 15), (15 + int(250 * pct / 100), 38),
                              (52, 152, 219), -1)
                cv2.putText(frame, f"{_capture_mode['count']}/{NIMGS}",
                            (175, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            (255, 255, 255), 2)
                if _capture_mode['count'] >= NIMGS:
                    _capture_mode['mode'] = None
                    recognizer.retrain(FACES_DIR)
                    # Encrypt-at-rest if enabled. The original training samples are
                    # left in place so retraining stays fast; an admin can choose
                    # /privacy/encrypt-now to encrypt the legacy crops too.
                    if (db.get_setting('encrypt_templates') or '0') == '1':
                        folder = os.path.join(
                            FACES_DIR,
                            f"{_capture_mode['username']}_{_capture_mode['userid']}")
                        try:
                            for f in os.listdir(folder):
                                p = os.path.join(folder, f)
                                if p.endswith('.jpg'):
                                    crypto_store.encrypt_file(p, p + '.enc',
                                                              remove_src=True)
                        except Exception as e:  # noqa: BLE001
                            log.warning('encrypt-on-enrol failed: %s', e)

            else:
                cv2.putText(frame, 'Idle', (15, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

            ok, buf = cv2.imencode('.jpg', frame)
            if not ok:
                continue
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route('/')
@login_required
def home():
    ensure_dirs()
    rows = db.list_attendance()
    present = sum(1 for r in rows if r['check_in'])
    return render_template(
        'home.html',
        rows=rows, l=len(rows), present=present,
        totalreg=total_registered(),
        nimgs=NIMGS,
        departments=db.list_departments(),
        yet_to_arrive=db.yet_to_arrive(),
        recent=db.recent_sightings(8),
        insights=db.smart_insights(),
        birthdays=db.birthdays_today(),
    )


@app.route('/video_feed')
@login_required
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/start_recognise', methods=['POST'])
@login_required
def start_recognise():
    if not recognizer.get().is_trained():
        return jsonify({'ok': False, 'msg': 'No trained model. Register a user first.'}), 400
    _vote_buffer.clear()
    _just_captured.clear()
    _capture_mode.update({'mode': 'recognise', 'count': 0, 'frame_idx': 0})
    return jsonify({'ok': True})


@app.route('/stop_capture', methods=['POST'])
@login_required
def stop_capture():
    _capture_mode.update({'mode': None, 'count': 0, 'frame_idx': 0})
    _vote_buffer.clear()
    _just_captured.clear()
    return jsonify({'ok': True})


@app.route('/capture_status')
@login_required
def capture_status():
    return jsonify({'mode': _capture_mode['mode'],
                    'count': _capture_mode['count'], 'target': NIMGS,
                    'sharp_avg': (_capture_mode['sharp_total'] / _capture_mode['count'])
                                  if _capture_mode['count'] else 0})


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@app.route('/add', methods=['POST'])
@login_required
def add():
    username = (request.form.get('newusername') or '').strip()
    userid = (request.form.get('newuserid') or '').strip()
    dept_id = request.form.get('department_id') or None
    email = (request.form.get('email') or '').strip() or None
    guardian_email = (request.form.get('guardian_email') or '').strip() or None
    dob = (request.form.get('date_of_birth') or '').strip() or None

    if not username or not userid:
        flash('Name and ID are both required.', 'danger')
        return redirect(url_for('home'))
    if not re.match(r'^[A-Za-z0-9 _.-]+$', username):
        flash('Name contains invalid characters.', 'danger')
        return redirect(url_for('home'))
    if not userid.isdigit():
        flash('ID must be numeric.', 'danger')
        return redirect(url_for('home'))

    safe_username = username.replace(' ', '_')
    folder = os.path.join(FACES_DIR, f'{safe_username}_{userid}')
    if os.path.isdir(folder) and os.listdir(folder):
        flash(f'A user with ID {userid} already exists.', 'warning')
        return redirect(url_for('home'))

    os.makedirs(folder, exist_ok=True)
    db.upsert_person(userid, username, int(dept_id) if dept_id else None,
                     email, guardian_email, dob)
    db.audit(session.get('admin'), 'add_user', f'{username} ({userid})')

    _capture_mode.update({'mode': 'register', 'username': safe_username,
                          'userid': userid, 'count': 0, 'frame_idx': 0,
                          'sharp_total': 0.0})
    return redirect(url_for('register_capture', name=safe_username, id=userid))


@app.route('/register_capture')
@login_required
def register_capture():
    return render_template('register.html',
                           name=request.args.get('name', ''),
                           uid=request.args.get('id', ''),
                           target=NIMGS,
                           totalreg=total_registered())


@app.route('/listusers')
@login_required
def list_users():
    q = request.args.get('q', '').strip()
    dept = request.args.get('dept') or None
    persons = db.list_persons(int(dept) if dept and dept.isdigit() else None, q)
    return render_template('listusers.html',
                           persons=persons,
                           departments=db.list_departments(),
                           q=q, selected_dept=dept,
                           totalreg=total_registered())


@app.route('/edituser', methods=['POST'])
@login_required
def edit_user():
    pid = (request.form.get('person_id') or '').strip()
    name = (request.form.get('name') or '').strip()
    dept_id = request.form.get('department_id') or None
    email = (request.form.get('email') or '').strip() or None
    if not pid:
        return redirect(url_for('list_users'))
    dob = (request.form.get('date_of_birth') or '').strip() or None
    phone = (request.form.get('phone') or '').strip() or None
    g_phone = (request.form.get('guardian_phone') or '').strip() or None
    db.update_person(pid, name=name or None,
                     department_id=int(dept_id) if dept_id else 0,
                     email=email, date_of_birth=dob,
                     guardian_email=(request.form.get('guardian_email') or '').strip() or None)
    # phone fields aren't in update_person yet — write directly
    from db import tx
    with tx() as c:
        c.execute('UPDATE persons SET phone = ?, guardian_phone = ? WHERE person_id = ?',
                  (phone, g_phone, pid))
    db.audit(session.get('admin'), 'edit_user', f'{pid}')
    flash('User updated.', 'success')
    return redirect(url_for('list_users'))


@app.route('/deleteuser')
@login_required
def delete_user():
    duser = request.args.get('user')
    if not duser:
        return redirect(url_for('list_users'))
    target = os.path.join(FACES_DIR, duser)
    if os.path.isdir(target):
        for n in os.listdir(target):
            try: os.remove(os.path.join(target, n))
            except OSError: pass
        try: os.rmdir(target)
        except OSError: pass

    _, pid = split_user_folder(duser)
    if pid:
        prof = os.path.join(PROFILE_DIR, f'{pid}.jpg')
        if os.path.exists(prof):
            os.remove(prof)
        db.delete_person(pid)
        db.audit(session.get('admin'), 'delete_user', duser)
    recognizer.retrain(FACES_DIR)
    flash(f'Removed user "{duser}".', 'success')
    return redirect(url_for('list_users'))


@app.route('/user/<pid>')
@login_required
def user_detail(pid):
    person = db.get_person(pid)
    if person is None:
        flash('User not found.', 'warning')
        return redirect(url_for('list_users'))
    days = int(request.args.get('days', '30'))
    rows = db.person_history(pid, days=days)
    present = sum(1 for r in rows if r['check_in'])
    late = sum(1 for r in rows if r['is_late'])
    return render_template(
        'user_detail.html',
        person=person, rows=rows, l=len(rows),
        present=present, late=late, days=days,
        departments=db.list_departments(),
    )


# ---------------------------------------------------------------------------
# Manual mark / check-out
# ---------------------------------------------------------------------------
@app.route('/manual_mark', methods=['POST'])
@login_required
def manual_mark():
    pid = (request.form.get('person_id') or '').strip()
    action = request.form.get('action') or 'in'
    if not pid:
        flash('Missing user ID.', 'danger'); return redirect(url_for('home'))
    work_start = db.get_setting('work_start_time', '09:00')
    late_min = int(db.get_setting('late_threshold_min', '15'))
    if action == 'out':
        db.manual_check_out(pid)
        msg = 'manual_check_out'
    else:
        db.manual_check_in(pid, work_start, late_min)
        msg = 'manual_check_in'
    db.audit(session.get('admin'), msg, pid)
    flash(f'Recorded {msg.replace("_", " ")} for #{pid}.', 'success')
    return redirect(request.referrer or url_for('home'))


# ---------------------------------------------------------------------------
# Bulk import
# ---------------------------------------------------------------------------
@app.route('/import_users', methods=['POST'])
@login_required
def import_users():
    f = request.files.get('csv')
    if not f:
        flash('No file uploaded.', 'danger')
        return redirect(url_for('list_users'))
    try:
        text = f.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text))
        n = 0
        for row in reader:
            name = (row.get('name') or row.get('Name') or '').strip()
            pid = (row.get('id') or row.get('ID') or '').strip()
            dept = (row.get('department') or '').strip()
            email = (row.get('email') or '').strip() or None
            if not name or not pid:
                continue
            dept_id = None
            if dept:
                db.add_department(dept)
                for d in db.list_departments():
                    if d['name'] == dept:
                        dept_id = d['id']; break
            db.upsert_person(pid, name, dept_id, email)
            n += 1
        db.audit(session.get('admin'), 'import_users', f'{n} rows')
        flash(f'Imported {n} users. Capture each user\'s face from the dashboard.', 'success')
    except Exception as e:  # noqa: BLE001
        flash(f'Import failed: {e}', 'danger')
    return redirect(url_for('list_users'))


# ---------------------------------------------------------------------------
# History / exports
# ---------------------------------------------------------------------------
@app.route('/history')
@login_required
def history():
    selected = request.args.get('date') or date.today().isoformat()
    rows = db.list_attendance(selected)
    try:
        pretty = datetime.strptime(selected, '%Y-%m-%d').strftime('%d %B %Y')
    except ValueError:
        pretty = selected
    return render_template('history.html',
                           rows=rows, l=len(rows),
                           selected=selected, selected_pretty=pretty,
                           dates=db.list_attendance_dates())


@app.route('/download')
@login_required
def download():
    fmt = request.args.get('fmt', 'csv')
    selected = request.args.get('date') or date.today().isoformat()
    rows = db.list_attendance(selected)
    df = pd.DataFrame(rows, columns=['person_id', 'person_name', 'department_name',
                                     'date', 'check_in', 'check_out', 'is_late'])
    df.rename(columns={'person_id': 'ID', 'person_name': 'Name',
                       'department_name': 'Department', 'date': 'Date',
                       'check_in': 'Check In', 'check_out': 'Check Out',
                       'is_late': 'Late'}, inplace=True)

    buf = io.BytesIO()
    if fmt == 'xlsx':
        with pd.ExcelWriter(buf, engine='openpyxl') as w:
            df.to_excel(w, index=False, sheet_name=f'Attendance_{selected}')
        buf.seek(0)
        return send_file(buf, as_attachment=True,
                         download_name=f'Attendance-{selected}.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return send_file(io.BytesIO(df.to_csv(index=False).encode('utf-8')),
                     as_attachment=True,
                     download_name=f'Attendance-{selected}.csv',
                     mimetype='text/csv')


# ---------------------------------------------------------------------------
@app.route('/analytics')
@login_required
def analytics():
    days = int(request.args.get('days', '7'))
    return render_template('analytics.html',
                           days=days,
                           summary=db.attendance_summary(days=days),
                           top=db.top_attenders(days=30, limit=5),
                           totalreg=total_registered())


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        text_keys = (
            'org_name', 'work_start_time', 'late_threshold_min',
            'recognition_threshold', 'min_checkout_gap_min',
            'camera_url', 'recognizer_backend',
            'smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from',
            'sms_url', 'sms_method', 'sms_headers_json',
            'sms_body_template', 'sms_from',
            'whatsapp_token', 'whatsapp_phone_id', 'whatsapp_template',
            'whatsapp_verify_token',
            'digest_recipients',
            'retention_visitors_days', 'retention_notifications_days',
            'retention_audit_days', 'retention_gps_days',
            'gps_accuracy_max_m', 'ppe_required',
            'temp_cutoff_c',
            'door_relay_url', 'door_relay_method', 'door_relay_body',
            'backup_local_dir', 'backup_put_url', 'backup_command',
        )
        for k in text_keys:
            v = request.form.get(k)
            if v is not None:
                db.set_setting(k, v.strip())

        # Boolean switches — checkbox absent = "0"
        for bk in ('liveness_enabled', 'encrypt_templates', 'site_mode_enabled',
                   'mask_required', 'backup_enabled'):
            db.set_setting(bk, '1' if request.form.get(bk) else '0')

        new_dept = (request.form.get('new_dept') or '').strip()
        if new_dept:
            db.add_department(new_dept)
        del_dept = request.form.get('del_dept')
        if del_dept and del_dept.isdigit():
            db.delete_department(int(del_dept))
        new_pw = request.form.get('new_password')
        if new_pw:
            db.change_admin_password(session['admin'], new_pw)
            flash('Password updated.', 'success')
        db.audit(session.get('admin'), 'update_settings', '')
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html',
                           settings=db.get_all_settings(),
                           departments=db.list_departments())


@app.route('/audit')
@login_required
def audit_view():
    return render_template('audit.html', entries=db.list_audit(200))


# ---------------------------------------------------------------------------
# Subjects & Sessions (college mode)
# ---------------------------------------------------------------------------
@app.route('/subjects', methods=['GET', 'POST'])
@login_required
def subjects():
    if request.method == 'POST':
        if request.form.get('delete'):
            sid = int(request.form['delete'])
            db.delete_subject(sid)
            db.audit(session.get('admin'), 'delete_subject', str(sid))
            flash('Subject removed.', 'success')
        else:
            name = (request.form.get('name') or '').strip()
            code = (request.form.get('code') or '').strip()
            dept = request.form.get('department_id') or None
            if not name:
                flash('Subject name required.', 'danger')
            else:
                db.add_subject(name, code, int(dept) if dept else None)
                db.audit(session.get('admin'), 'add_subject', name)
                flash('Subject added.', 'success')
        return redirect(url_for('subjects'))

    return render_template('subjects.html',
                           subjects=db.list_subjects(),
                           departments=db.list_departments())


@app.route('/sessions', methods=['GET', 'POST'])
@login_required
def sessions():
    if request.method == 'POST':
        if request.form.get('delete'):
            sid = int(request.form['delete'])
            db.delete_session(sid)
            db.audit(session.get('admin'), 'delete_session', str(sid))
            # If we deleted the active session, clear it
            if (db.get_setting('active_session_id') or '') == str(sid):
                db.set_setting('active_session_id', '')
            flash('Session removed.', 'success')
        else:
            sub = request.form.get('subject_id')
            dt  = request.form.get('date') or date.today().isoformat()
            st  = request.form.get('start_time') or '09:00'
            et  = request.form.get('end_time') or '10:00'
            notes = (request.form.get('notes') or '').strip()
            if not sub:
                flash('Subject is required.', 'danger')
            else:
                new_id = db.create_session(int(sub), dt, st, et, notes)
                db.audit(session.get('admin'), 'create_session', str(new_id))
                if request.form.get('start_now') == '1':
                    db.set_setting('active_session_id', str(new_id))
                    flash('Session created and activated.', 'success')
                else:
                    flash('Session created.', 'success')
        return redirect(url_for('sessions'))

    filt_date = request.args.get('date') or date.today().isoformat()
    return render_template('sessions.html',
                           sessions=db.list_sessions(filt_date),
                           subjects=db.list_subjects(),
                           selected_date=filt_date,
                           active_session_id=db.get_setting('active_session_id'))


@app.route('/session/<int:sid>')
@login_required
def session_detail(sid):
    s = db.get_session(sid)
    if not s:
        flash('Session not found.', 'warning')
        return redirect(url_for('sessions'))
    return render_template('session_detail.html',
                           session=s,
                           present=db.session_present(sid))


@app.route('/set_active_session', methods=['POST'])
@login_required
def set_active_session():
    sid = (request.form.get('session_id') or '').strip()
    if sid and not sid.isdigit():
        flash('Invalid session.', 'danger')
        return redirect(request.referrer or url_for('home'))
    db.set_setting('active_session_id', sid)
    db.audit(session.get('admin'), 'set_active_session', sid)
    flash('Active session set.' if sid else 'Active session cleared.', 'success')
    return redirect(request.referrer or url_for('home'))


@app.route('/defaulters')
@login_required
def defaulters():
    pct = float(request.args.get('pct') or db.get_setting('attendance_required_pct', '75'))
    return render_template('defaulters.html',
                           threshold_pct=pct,
                           rows=db.all_defaulters(pct))


@app.route('/contacts.csv')
@login_required
def contacts_csv():
    """Export student+guardian contacts for bulk mail/SMS."""
    persons = db.list_persons()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Roll No', 'Name', 'Department', 'Email', 'Guardian Email'])
    for p in persons:
        w.writerow([p['person_id'], p['name'], p['department_name'] or '',
                    p['email'] or '', p['guardian_email'] or ''])
    return Response(buf.getvalue(),
                    mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=contacts.csv'})


@app.route('/register/print')
@login_required
def register_print():
    """Print-friendly attendance register for a chosen date."""
    selected = request.args.get('date') or date.today().isoformat()
    rows = db.list_attendance(selected)
    sessions_today = db.list_sessions(selected)
    try:
        pretty = datetime.strptime(selected, '%Y-%m-%d').strftime('%d %B %Y')
    except ValueError:
        pretty = selected
    return render_template('register_print.html',
                           rows=rows, sessions=sessions_today,
                           selected=selected, selected_pretty=pretty)


# ---------------------------------------------------------------------------
# JSON APIs (polled by the dashboard)
# ---------------------------------------------------------------------------
@app.route('/api/stats')
@login_required
def api_stats():
    rows = db.list_attendance()
    return jsonify({
        'total_registered': total_registered(),
        'present_today': len(rows),
        'late_today': sum(1 for r in rows if r['is_late']),
        'recognizer': recognizer.get().name,
    })


@app.route('/api/recent')
@login_required
def api_recent():
    return jsonify({'recent': db.recent_sightings(8)})


@app.route('/docs')
@login_required
def docs_download():
    """Serve the product-documentation PDF as a download."""
    path = os.path.join('docs', 'FaceMark-Documentation.pdf')
    if not os.path.exists(path):
        flash('Documentation PDF not generated yet. '
              'Run: python build_docs.py', 'warning')
        return redirect(url_for('home'))
    return send_file(path, as_attachment=True,
                     download_name='FaceMark-Documentation.pdf',
                     mimetype='application/pdf')


@app.route('/api/insights')
@login_required
def api_insights():
    return jsonify({'insights': db.smart_insights(),
                    'birthdays': db.birthdays_today()})


@app.route('/api/heatmap/<pid>')
@login_required
def api_heatmap(pid):
    days = int(request.args.get('days', '90'))
    return jsonify(db.attendance_heatmap(pid, days))


# ---------------------------------------------------------------------------
# Kiosk + Big-screen display + Visitors + ID Cards
# ---------------------------------------------------------------------------
@app.route('/kiosk')
@login_required
def kiosk():
    """Fullscreen, no-chrome attendance terminal for a tablet at the door."""
    return render_template('kiosk.html',
                           org_name=db.get_setting('org_name'))


@app.route('/display')
@login_required
def display_board():
    """Hallway / projector display — big numbers, last arrivals."""
    rows = db.list_attendance()
    return render_template('display.html',
                           org_name=db.get_setting('org_name'),
                           present_count=len(rows),
                           total_reg=total_registered())


@app.route('/visitors')
@login_required
def visitors():
    if request.method == 'POST':
        db.clear_visitors()
        return redirect(url_for('visitors'))
    return render_template('visitors.html', visitors=db.list_visitors(200))


@app.route('/visitors/clear', methods=['POST'])
@login_required
def visitors_clear():
    db.clear_visitors()
    for f in os.listdir(VISITOR_DIR):
        try: os.remove(os.path.join(VISITOR_DIR, f))
        except OSError: pass
    db.audit(session.get('admin'), 'clear_visitors', '')
    flash('Visitor log cleared.', 'success')
    return redirect(url_for('visitors'))


@app.route('/idcard/<pid>')
@login_required
def idcard(pid):
    person = db.get_person(pid)
    if person is None:
        flash('User not found.', 'warning')
        return redirect(url_for('list_users'))
    return render_template('idcard.html', person=person)


@app.route('/api/just_captured')
@login_required
def api_just_captured():
    """Anyone whose capture flash is still active right now."""
    now_ts = time.time()
    out = []
    for pid, info in list(_just_captured.items()):
        if info['expires'] < now_ts:
            continue
        person = db.get_person(pid)
        out.append({
            'person_id': pid,
            'name': person['name'] if person else info['name'],
            'department': person['department_name'] if person else None,
            'expires_in': round(info['expires'] - now_ts, 2),
        })
    return jsonify({'captured': out})


# ===========================================================================
# Branches / sites
# ===========================================================================
@app.route('/branches', methods=['GET', 'POST'])
@role_required('admin', 'hr')
def branches():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_branch(int(request.form['delete']))
        else:
            db.add_branch(
                request.form.get('name', '').strip(),
                request.form.get('address', ''),
                request.form.get('timezone', ''),
                float(request.form['lat']) if request.form.get('lat') else None,
                float(request.form['lng']) if request.form.get('lng') else None,
                float(request.form['radius_m']) if request.form.get('radius_m') else None,
                request.form.get('polygon_json') or None)
        db.audit(session.get('admin'), 'branches', request.form.get('name', ''))
        return redirect(url_for('branches'))
    return render_template('branches.html', branches=db.list_branches())


# ===========================================================================
# Shifts
# ===========================================================================
@app.route('/shifts', methods=['GET', 'POST'])
@role_required('admin', 'hr')
def shifts():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_shift(int(request.form['delete']))
        else:
            db.add_shift(
                request.form.get('name', '').strip(),
                request.form.get('start_time', '09:00'),
                request.form.get('end_time', '18:00'),
                int(request.form.get('grace_min', '10')),
                int(request.form['branch_id']) if request.form.get('branch_id') else None,
                int(request.form['department_id']) if request.form.get('department_id') else None,
                request.form.get('days_mask', '1111100'))
        return redirect(url_for('shifts'))
    return render_template('shifts.html',
                           shifts=db.list_shifts(),
                           branches=db.list_branches(),
                           departments=db.list_departments())


@app.route('/shifts/assign', methods=['POST'])
@role_required('admin', 'hr')
def shifts_assign():
    pid = request.form['person_id']
    sid = int(request.form['shift_id'])
    eff = request.form.get('effective') or date.today().isoformat()
    db.assign_shift(pid, sid, eff)
    flash(f'Assigned shift to {pid} from {eff}.', 'success')
    return redirect(request.referrer or url_for('shifts'))


# ===========================================================================
# Leave requests
# ===========================================================================
@app.route('/leaves', methods=['GET', 'POST'])
@login_required
def leaves():
    role = session.get('role') or 'admin'
    if request.method == 'POST':
        # admins/HR decide; everyone else creates
        if request.form.get('decide') and role in ('admin', 'hr'):
            db.decide_leave(int(request.form['leave_id']),
                            request.form['decide'],
                            session.get('admin'))
        else:
            db.add_leave(
                request.form['person_id'],
                request.form.get('leave_type', 'sick'),
                request.form['start_date'],
                request.form['end_date'],
                request.form.get('reason', ''))
        return redirect(url_for('leaves'))
    return render_template('leaves.html',
                           pending=db.list_leaves('pending'),
                           recent=db.list_leaves(),
                           persons=db.list_persons())


# ===========================================================================
# Timetable + holidays
# ===========================================================================
@app.route('/timetable', methods=['GET', 'POST'])
@role_required('admin', 'hr', 'teacher')
def timetable():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_timetable_slot(int(request.form['delete']))
        else:
            db.add_timetable_slot(
                int(request.form['subject_id']),
                int(request.form['weekday']),
                request.form['start_time'],
                request.form['end_time'],
                request.form.get('room', ''),
                request.form.get('teacher', ''),
                request.form.get('active_from') or None,
                request.form.get('active_to') or None)
        return redirect(url_for('timetable'))
    return render_template('timetable.html',
                           slots=db.list_timetable(),
                           subjects=db.list_subjects())


@app.route('/timetable/materialise', methods=['POST'])
@role_required('admin', 'hr', 'teacher')
def timetable_materialise():
    today = request.form.get('date') or date.today().isoformat()
    n = db.materialise_today_sessions(today)
    flash(f'Created {n} sessions from timetable for {today}.', 'success')
    return redirect(url_for('timetable'))


@app.route('/holidays', methods=['GET', 'POST'])
@role_required('admin', 'hr')
def holidays():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_holiday(int(request.form['delete']))
        else:
            db.add_holiday(
                request.form['date'],
                request.form['name'],
                int(request.form['branch_id']) if request.form.get('branch_id') else None)
        return redirect(url_for('holidays'))
    return render_template('holidays.html',
                           holidays=db.list_holidays(),
                           branches=db.list_branches())


# ===========================================================================
# DPDP / GDPR consent + erasure
# ===========================================================================
@app.route('/privacy')
@role_required('admin', 'hr')
def privacy_console():
    return render_template('privacy.html',
                           consents=db.list_consents(),
                           erasures=db.list_erasure_requests(),
                           settings=db.get_all_settings())


@app.route('/privacy/consent', methods=['POST'])
@login_required
def privacy_consent():
    pid = request.form['person_id']
    purpose = request.form.get('purpose', 'biometric')
    granted = request.form.get('granted') == '1'
    proof = request.form.get('proof_text', '')
    db.record_consent(pid, purpose, granted, proof)
    db.audit(session.get('admin'), 'consent',
             f'{pid} {purpose}={"yes" if granted else "no"}')
    flash('Consent recorded.', 'success')
    return redirect(request.referrer or url_for('privacy_console'))


@app.route('/privacy/erase/<pid>', methods=['POST'])
@role_required('admin')
def privacy_erase(pid):
    req_id = db.request_erasure(pid, session.get('admin'))
    # Delete all biometric files
    safe = os.path.join(FACES_DIR)
    for d in os.listdir(safe):
        if d.endswith(f'_{pid}'):
            full = os.path.join(safe, d)
            for f in os.listdir(full):
                try: os.remove(os.path.join(full, f))
                except OSError: pass
            try: os.rmdir(full)
            except OSError: pass
    prof = os.path.join(PROFILE_DIR, f'{pid}.jpg')
    if os.path.exists(prof):
        os.remove(prof)
    db.fulfil_erasure(pid)
    recognizer.retrain(FACES_DIR)
    db.audit(session.get('admin'), 'erasure', pid)
    flash(f'Erasure completed for {pid} (request #{req_id}).', 'success')
    return redirect(url_for('privacy_console'))


@app.route('/privacy/encrypt-now', methods=['POST'])
@role_required('admin')
def privacy_encrypt_now():
    n = 0
    for d in os.listdir(FACES_DIR):
        folder = os.path.join(FACES_DIR, d)
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            if f.endswith('.jpg'):
                p = os.path.join(folder, f)
                try:
                    crypto_store.encrypt_file(p, p + '.enc', remove_src=True)
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
    db.set_setting('encrypt_templates', '1')
    flash(f'Encrypted {n} files. Future enrolments are encrypted automatically.', 'success')
    return redirect(url_for('privacy_console'))


# ===========================================================================
# API keys + webhooks admin
# ===========================================================================
@app.route('/api-keys', methods=['GET', 'POST'])
@role_required('admin')
def api_keys():
    new_raw = None
    if request.method == 'POST':
        if request.form.get('revoke'):
            db.revoke_api_key(int(request.form['revoke']))
        else:
            new_raw, _ = db.create_api_key(
                request.form.get('label', 'new key'),
                request.form.get('scopes', 'read'))
            flash('Copy this key now — it won\'t be shown again.', 'warning')
    return render_template('api_keys.html',
                           keys=db.list_api_keys(),
                           webhooks=db.list_webhooks(),
                           new_raw=new_raw)


@app.route('/webhooks', methods=['POST'])
@role_required('admin')
def webhooks_admin():
    if request.form.get('delete'):
        db.delete_webhook(int(request.form['delete']))
    else:
        db.add_webhook(request.form['url'],
                       request.form.get('events', 'check_in,check_out'),
                       request.form.get('secret', ''))
    return redirect(url_for('api_keys'))


# ===========================================================================
# RBAC admins
# ===========================================================================
@app.route('/admins', methods=['GET', 'POST'])
@role_required('admin')
def admins():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_admin(int(request.form['delete']))
        else:
            db.create_admin(
                request.form['username'],
                request.form['password'],
                request.form.get('role', 'staff'),
                request.form.get('email'),
                int(request.form['branch_id']) if request.form.get('branch_id') else None)
        return redirect(url_for('admins'))
    return render_template('admins.html',
                           admins=db.list_admins(),
                           branches=db.list_branches(),
                           roles=ROLE_LEVELS)


# ===========================================================================
# Payroll
# ===========================================================================
@app.route('/payroll', methods=['GET'])
@role_required('admin', 'hr')
def payroll_view():
    start = request.args.get('start') or date.today().replace(day=1).isoformat()
    end = request.args.get('end') or date.today().isoformat()
    rows = payroll.compute(start, end)
    return render_template('payroll.html', rows=rows, start=start, end=end)


@app.route('/payroll/export')
@role_required('admin', 'hr')
def payroll_export():
    start = request.args.get('start') or date.today().replace(day=1).isoformat()
    end = request.args.get('end') or date.today().isoformat()
    fmt = request.args.get('fmt', 'csv')
    rows = payroll.compute(start, end)
    df = pd.DataFrame(rows)
    if fmt == 'xlsx':
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as w:
            df.to_excel(w, index=False, sheet_name='Payroll')
        buf.seek(0)
        return send_file(buf, as_attachment=True,
                         download_name=f'Payroll-{start}_{end}.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return send_file(io.BytesIO(df.to_csv(index=False).encode()),
                     as_attachment=True,
                     download_name=f'Payroll-{start}_{end}.csv',
                     mimetype='text/csv')


# ===========================================================================
# Construction Site Edition: contractors + muster + PPE
# ===========================================================================
@app.route('/site/contractors', methods=['GET', 'POST'])
@role_required('admin', 'hr')
def contractors():
    if request.method == 'POST':
        db.add_contractor(
            request.form['name'],
            request.form.get('contact', ''),
            int(request.form['site_id']) if request.form.get('site_id') else None)
        return redirect(url_for('contractors'))
    return render_template('contractors.html',
                           contractors=db.list_contractors(),
                           branches=db.list_branches())


@app.route('/site/muster', methods=['GET', 'POST'])
@role_required('admin', 'hr')
def site_muster():
    on_date = request.args.get('date') or date.today().isoformat()
    branch_id = request.args.get('branch_id')
    branch_id_int = int(branch_id) if branch_id and branch_id.isdigit() else None
    if request.method == 'POST':
        db.upsert_muster(
            request.form['person_id'], on_date,
            float(request.form.get('hours', '8')),
            float(request.form.get('daily_rate', '0')),
            float(request.form.get('overtime_hr', '0')),
            branch_id_int,
            int(request.form['contractor_id']) if request.form.get('contractor_id') else None,
            request.form.get('note', ''))
        return redirect(url_for('site_muster', date=on_date, branch_id=branch_id or ''))
    return render_template('site_muster.html',
                           date=on_date,
                           branch_id=branch_id_int,
                           branches=db.list_branches(),
                           contractors=db.list_contractors(),
                           persons=db.list_persons(),
                           rows=payroll.site_wages(on_date, branch_id_int))


@app.route('/site/ppe')
@role_required('admin', 'hr')
def ppe_view():
    return render_template('ppe.html', incidents=db.list_ppe_incidents())


# ===========================================================================
# Self-service / parent portal
# ===========================================================================
@app.route('/portal/login', methods=['GET', 'POST'])
@rl('10/m', key='portal-login')
def portal_login():
    if request.method == 'POST':
        pid = request.form.get('person_id', '').strip()
        pin = request.form.get('pin', '').strip()
        if db.verify_person_pin(pid, pin):
            session['portal_pid'] = pid
            return redirect(url_for('portal_home'))
        flash('Invalid ID or PIN.', 'danger')
    return render_template('portal_login.html')


@app.route('/portal/logout')
def portal_logout():
    session.pop('portal_pid', None)
    return redirect(url_for('portal_login'))


@app.route('/portal')
@portal_required
def portal_home():
    pid = session['portal_pid']
    person = db.get_person(pid)
    if not person:
        return redirect(url_for('portal_logout'))
    rows = db.person_history(pid, 30)
    return render_template('portal_home.html',
                           person=dict(person),
                           rows=rows,
                           leaves=db.list_leaves(person_id=pid))


@app.route('/portal/leave', methods=['POST'])
@portal_required
def portal_leave():
    pid = session['portal_pid']
    db.add_leave(pid,
                 request.form.get('leave_type', 'sick'),
                 request.form['start_date'],
                 request.form['end_date'],
                 request.form.get('reason', ''))
    flash('Leave requested.', 'success')
    return redirect(url_for('portal_home'))


# ===========================================================================
# GPS / mobile check-in
# ===========================================================================
@app.route('/api/gps/check_in', methods=['POST'])
@rl('30/m', key='gps-checkin')
def gps_check_in():
    """Public endpoint used by the PWA. Auth = portal session OR person PIN."""
    j = request.get_json(force=True, silent=True) or {}
    pid = j.get('person_id') or session.get('portal_pid')
    if not pid:
        return jsonify({'ok': False, 'error': 'auth-required'}), 401
    # If not in portal session, require PIN
    if not session.get('portal_pid'):
        if not db.verify_person_pin(pid, j.get('pin', '')):
            return jsonify({'ok': False, 'error': 'bad-pin'}), 401

    try:
        lat = float(j['lat']); lng = float(j['lng'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'coords-required'}), 400
    accuracy = float(j.get('accuracy', 0) or 0)
    fix = {'lat': lat, 'lng': lng, 'accuracy': accuracy,
           'ts': datetime.now().isoformat(timespec='seconds'),
           'mocked': bool(j.get('mocked'))}

    # Mock-GPS detection
    prev = db.last_gps_mark(pid)
    mock_reason = geo.looks_mocked(
        fix, previous=(prev and {'lat': prev['lat'], 'lng': prev['lng'],
                                 'ts': prev['created_at']}),
        accuracy_max_m=float(db.get_setting('gps_accuracy_max_m') or '60'))

    if mock_reason:
        db.log_gps_mark(pid, None, lat, lng, accuracy, 'rejected', mock_reason)
        return jsonify({'ok': False, 'error': f'rejected:{mock_reason}'}), 403

    # Find a branch the user is inside
    matched_branch = None
    for b in db.list_branches():
        inside, _ = geo.inside_site(b, lat, lng)
        if inside:
            matched_branch = b; break
    if matched_branch is None and db.list_branches():
        db.log_gps_mark(pid, None, lat, lng, accuracy, 'rejected', 'outside-all-sites')
        return jsonify({'ok': False, 'error': 'outside-geofence'}), 403

    bid = matched_branch['id'] if matched_branch else None
    db.log_gps_mark(pid, bid, lat, lng, accuracy, 'accepted', '')
    ws = db.get_setting('work_start_time', '09:00')
    lt = int(db.get_setting('late_threshold_min', '15'))
    res = db.mark_attendance(pid, ws, lt, int(db.get_setting('min_checkout_gap_min', '30')))
    dispatch_event(res.get('event', 'check_in'), {'person_id': pid, **res, 'via': 'gps'})
    return jsonify({'ok': True, 'event': res.get('event'),
                    'time': res.get('time'),
                    'branch': matched_branch['name'] if matched_branch else None})


# ===========================================================================
# Kiosk PIN / QR fallback (when face fails)
# ===========================================================================
@app.route('/kiosk/pin', methods=['POST'])
@rl('20/m', key='kiosk-pin')
def kiosk_pin():
    pid = (request.form.get('person_id') or '').strip()
    pin = (request.form.get('pin') or '').strip()
    if not db.verify_person_pin(pid, pin):
        return jsonify({'ok': False, 'error': 'bad-pin'}), 401
    ws = db.get_setting('work_start_time', '09:00')
    lt = int(db.get_setting('late_threshold_min', '15'))
    res = db.mark_attendance(pid, ws, lt,
                             int(db.get_setting('min_checkout_gap_min', '30')))
    dispatch_event(res.get('event', 'check_in'), {'person_id': pid, **res, 'via': 'pin'})
    return jsonify({'ok': True, **res})


@app.route('/kiosk/qr', methods=['POST'])
@rl('30/m', key='kiosk-qr')
def kiosk_qr():
    pid = (request.form.get('person_id') or '').strip()
    sec = (request.form.get('qr') or '').strip()
    if not db.verify_qr_secret(pid, sec):
        return jsonify({'ok': False, 'error': 'bad-qr'}), 401
    ws = db.get_setting('work_start_time', '09:00')
    lt = int(db.get_setting('late_threshold_min', '15'))
    res = db.mark_attendance(pid, ws, lt,
                             int(db.get_setting('min_checkout_gap_min', '30')))
    dispatch_event(res.get('event', 'check_in'), {'person_id': pid, **res, 'via': 'qr'})
    return jsonify({'ok': True, **res})


@app.route('/user/<pid>/pin', methods=['POST'])
@role_required('admin', 'hr')
def set_pin(pid):
    pin = request.form.get('pin', '').strip()
    if len(pin) < 4:
        flash('PIN must be at least 4 digits.', 'danger')
        return redirect(url_for('user_detail', pid=pid))
    db.set_person_pin(pid, pin)
    flash('PIN set.', 'success')
    return redirect(url_for('user_detail', pid=pid))


@app.route('/user/<pid>/qr')
@role_required('admin', 'hr')
def rotate_qr(pid):
    db.rotate_qr_secret(pid)
    flash('New QR secret generated.', 'success')
    return redirect(url_for('idcard', pid=pid))


# ===========================================================================
# At-risk analytics
# ===========================================================================
@app.route('/at-risk')
@role_required('admin', 'hr', 'teacher')
def at_risk():
    return render_template('at_risk.html', rows=db.at_risk_persons())


# ===========================================================================
# PWA manifest + service worker
# ===========================================================================
@app.route('/manifest.json')
def manifest():
    return jsonify({
        'name': db.get_setting('org_name') or 'FaceMark',
        'short_name': 'FaceMark',
        'start_url': '/portal',
        'display': 'standalone',
        'theme_color': '#1f2c5b',
        'background_color': '#0d1d3a',
        'icons': [
            {'src': '/static/profiles/_default.png', 'sizes': '192x192',
             'type': 'image/png'},
        ],
    })


@app.route('/service-worker.js')
def service_worker():
    js = """
    const CACHE='fm-v1';
    self.addEventListener('install', e => {
      e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/portal','/portal/login'])));
    });
    self.addEventListener('fetch', e => {
      e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
    });
    """
    return Response(js, mimetype='application/javascript')


# ===========================================================================
# RFID kiosk fallback
# ===========================================================================
@app.route('/kiosk/rfid', methods=['POST'])
@rl('60/m', key='kiosk-rfid')
def kiosk_rfid():
    uid = (request.form.get('uid') or request.json.get('uid', '') if request.is_json else
           request.form.get('uid', '')).strip()
    if not uid:
        return jsonify({'ok': False, 'error': 'uid-required'}), 400
    person = db.find_person_by_rfid(uid)
    if not person:
        return jsonify({'ok': False, 'error': 'rfid-not-registered'}), 404
    pid = person['person_id']
    ws = db.get_setting('work_start_time', '09:00')
    lt = int(db.get_setting('late_threshold_min', '15'))
    res = db.mark_attendance(pid, ws, lt,
                             int(db.get_setting('min_checkout_gap_min', '30')))
    dispatch_event(res.get('event', 'check_in'),
                   {'person_id': pid, 'via': 'rfid', **res})
    return jsonify({'ok': True, 'person_id': pid, 'name': person['name'], **res})


@app.route('/user/<pid>/rfid', methods=['POST'])
@role_required('admin', 'hr')
def set_rfid(pid):
    uid = request.form.get('uid', '').strip()
    if not uid:
        flash('Tap a card to read its UID first.', 'danger')
        return redirect(url_for('user_detail', pid=pid))
    db.set_person_rfid(pid, uid)
    flash(f'RFID linked: {uid}', 'success')
    return redirect(url_for('user_detail', pid=pid))


# ===========================================================================
# Multi-camera registry
# ===========================================================================
@app.route('/cameras', methods=['GET', 'POST'])
@role_required('admin', 'hr')
def cameras():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_camera(int(request.form['delete']))
        elif request.form.get('toggle'):
            cid = int(request.form['toggle'])
            cam = db.get_camera(cid)
            db.toggle_camera(cid, not bool(cam['enabled']))
        else:
            db.add_camera(
                request.form['name'].strip(),
                request.form['url'].strip(),
                int(request.form['branch_id']) if request.form.get('branch_id') else None,
                request.form.get('purpose', 'attendance'))
        return redirect(url_for('cameras'))
    return render_template('cameras.html',
                           cameras=db.list_cameras(),
                           branches=db.list_branches())


@app.route('/cameras/<int:cid>/feed')
@login_required
def cameras_feed(cid):
    """Live MJPEG from a non-default camera. Same recognise pipeline but
    swapped source. For now, sets the source and redirects to /video_feed."""
    cam = db.get_camera(cid)
    if not cam:
        return jsonify({'ok': False, 'error': 'no-camera'}), 404
    db.set_setting('camera_url', cam['url'])
    return redirect(url_for('video_feed'))


# ===========================================================================
# Bus / Transport
# ===========================================================================
@app.route('/transport', methods=['GET', 'POST'])
@role_required('admin', 'hr', 'teacher')
def transport():
    if request.method == 'POST':
        if request.form.get('delete'):
            db.delete_bus_route(int(request.form['delete']))
        else:
            db.add_bus_route(
                request.form['name'],
                request.form.get('driver', ''),
                request.form.get('vehicle_no', ''),
                int(request.form['branch_id']) if request.form.get('branch_id') else None)
        return redirect(url_for('transport'))
    routes = db.list_bus_routes()
    return render_template('transport.html',
                           routes=routes,
                           boardings=db.list_boardings(100),
                           branches=db.list_branches())


@app.route('/transport/<int:rid>/stops', methods=['GET', 'POST'])
@role_required('admin', 'hr', 'teacher')
def transport_stops(rid):
    if request.method == 'POST':
        db.add_bus_stop(rid,
                        request.form['name'],
                        int(request.form.get('seq') or '0'),
                        float(request.form['lat']) if request.form.get('lat') else None,
                        float(request.form['lng']) if request.form.get('lng') else None)
        return redirect(url_for('transport_stops', rid=rid))
    return render_template('transport_stops.html',
                           rid=rid, stops=db.list_bus_stops(rid))


@app.route('/api/transport/board', methods=['POST'])
def transport_board_api():
    """Called from a tablet at the bus door — face recognised + boarding event."""
    j = request.get_json(force=True, silent=True) or {}
    pid = j.get('person_id')
    if not pid:
        return jsonify({'ok': False, 'error': 'person_id-required'}), 400
    direction = j.get('direction', 'board')
    bid = db.log_boarding(pid, j.get('route_id'), j.get('stop_id'),
                          direction, j.get('location', ''))
    # Notify guardian
    person = db.get_person(pid)
    if person:
        person = dict(person)
        org = db.get_setting('org_name') or 'FaceMark'
        body = (f"{org}: {person['name']} {'boarded' if direction == 'board' else 'alighted'} "
                f"the bus at {datetime.now().strftime('%H:%M')}.")
        for ch, key in (('email', 'guardian_email'), ('auto', 'guardian_phone')):
            to = person.get(key)
            if to:
                notify.dispatch(ch, to, 'Bus update', body)
    dispatch_event('bus_' + direction, {'person_id': pid, 'route_id': j.get('route_id')})
    return jsonify({'ok': True, 'boarding_id': bid})


# ===========================================================================
# Substitute teacher handling
# ===========================================================================
@app.route('/substitutions', methods=['GET', 'POST'])
@role_required('admin', 'hr', 'teacher')
def substitutions_page():
    if request.method == 'POST':
        db.add_substitution(
            int(request.form['session_id']),
            request.form['substitute'],
            request.form.get('original', ''),
            request.form.get('note', ''))
        flash('Substitution recorded.', 'success')
        return redirect(url_for('substitutions_page'))
    today = date.today().isoformat()
    return render_template(
        'substitutions.html',
        rows=db.list_substitutions(),
        sessions=db.list_sessions(today))


# ===========================================================================
# Temperature ingest + door relay test
# ===========================================================================
@app.route('/api/sensor/temperature', methods=['POST'])
def sensor_temperature():
    """POST {person_id, temperature_c[, branch_id]} — typically called from
    a thermal terminal SDK or an ESP32 with an MLX90614."""
    j = request.get_json(force=True, silent=True) or {}
    pid = j.get('person_id')
    temp = j.get('temperature_c')
    if pid is None or temp is None:
        return jsonify({'ok': False, 'error': 'person_id+temperature_c required'}), 400
    res = safety.check_temperature(pid, j.get('branch_id'), float(temp))
    if res['blocked']:
        # Notify guardian + log a fever event
        person = db.get_person(pid)
        if person:
            person = dict(person)
            gp = person.get('guardian_phone')
            if gp:
                notify.dispatch('auto', gp, 'Fever alert',
                                f"{person['name']} flagged {temp}°C at the gate.")
        dispatch_event('fever', {'person_id': pid, 'temp_c': temp})
    return jsonify({'ok': True, **res})


@app.route('/door/test', methods=['POST'])
@role_required('admin')
def door_test():
    out = safety.trigger_door(None, None)
    return jsonify(out)


# ===========================================================================
# Exam continuous-presence
# ===========================================================================
@app.route('/exams', methods=['GET', 'POST'])
@role_required('admin', 'hr', 'teacher')
def exams():
    if request.method == 'POST':
        if request.form.get('end'):
            db.end_exam(int(request.form['end']))
        else:
            db.create_exam(
                request.form['name'],
                request.form['start_at'],
                request.form['end_at'],
                int(request.form['branch_id']) if request.form.get('branch_id') else None,
                int(request.form['department_id']) if request.form.get('department_id') else None,
                int(request.form.get('check_every_sec', '60')))
        return redirect(url_for('exams'))
    return render_template(
        'exams.html',
        exams=db.list_exams(),
        active=db.get_active_exam(),
        branches=db.list_branches(),
        departments=db.list_departments())


@app.route('/exams/<int:eid>')
@role_required('admin', 'hr', 'teacher')
def exam_detail(eid):
    return render_template('exam_detail.html',
                           alerts=db.list_exam_alerts(eid),
                           exam_id=eid)


# ===========================================================================
# Per-person export (DPDP / GDPR Art. 20)
# ===========================================================================
@app.route('/privacy/export/<pid>')
@role_required('admin', 'hr')
def privacy_export(pid):
    """Bundle every byte we hold on this person into a ZIP — JSON dump,
    face crops (encrypted or not), profile thumb, attendance, leaves,
    sensor and PPE logs."""
    import zipfile
    import json as _json
    person = db.get_person(pid)
    if not person:
        flash('No such person.', 'warning')
        return redirect(url_for('list_users'))
    payload = db.person_full_export(pid)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('person.json', _json.dumps(payload, indent=2, default=str))
        # face crops
        safe = person['name'].replace(' ', '_') + '_' + pid
        folder = os.path.join(FACES_DIR, safe)
        if os.path.isdir(folder):
            for n in os.listdir(folder):
                p = os.path.join(folder, n)
                z.write(p, arcname=f'faces/{n}')
        thumb = os.path.join(PROFILE_DIR, f'{pid}.jpg')
        if os.path.exists(thumb):
            z.write(thumb, arcname=f'profile/{pid}.jpg')
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f'facemark-export-{pid}.zip',
                     mimetype='application/zip')


# ===========================================================================
# Cloud backup
# ===========================================================================
@app.route('/backup', methods=['GET', 'POST'])
@role_required('admin')
def backup_view():
    if request.method == 'POST':
        if request.form.get('run'):
            res = cloud_backup.run_backup_once()
            flash(f"Backup: {res['status']} → {res['dest']} ({res['bytes']} b)",
                  'success' if res['status'] == 'ok' else 'warning')
        else:
            for k in ('backup_local_dir', 'backup_put_url', 'backup_command'):
                v = request.form.get(k)
                if v is not None:
                    db.set_setting(k, v.strip())
            db.set_setting('backup_enabled',
                           '1' if request.form.get('backup_enabled') else '0')
        return redirect(url_for('backup_view'))
    return render_template('backup.html',
                           logs=db.list_backups(),
                           settings=db.get_all_settings())


# ===========================================================================
# WhatsApp two-way bot (D5)
# ===========================================================================
@app.route('/webhook/whatsapp', methods=['GET', 'POST'])
def whatsapp_webhook():
    """Meta WhatsApp webhook endpoint.

    GET = subscription handshake (echo hub.challenge).
    POST = incoming message; we answer:
       - "present <id>"  -> current day's status
       - "out <id>"      -> last check-out time
       - "help"          -> usage
    """
    verify_token = db.get_setting('whatsapp_verify_token') or 'facemark-verify'
    if request.method == 'GET':
        if (request.args.get('hub.mode') == 'subscribe'
                and request.args.get('hub.verify_token') == verify_token):
            return request.args.get('hub.challenge', ''), 200
        return 'forbidden', 403

    j = request.get_json(force=True, silent=True) or {}
    try:
        entry = j['entry'][0]['changes'][0]['value']
        msg = entry['messages'][0]
        from_ = msg['from']
        body = (msg.get('text', {}).get('body') or '').strip().lower()
    except Exception:  # noqa: BLE001
        return jsonify({'ok': True})

    org = db.get_setting('org_name') or 'FaceMark'
    if body.startswith('help') or body == '?':
        reply = (f'{org} bot:\n'
                 'present <id>  - today\'s check-in\n'
                 'out <id>      - today\'s check-out\n'
                 'help          - this message')
    elif body.startswith(('present', 'out')):
        parts = body.split()
        if len(parts) < 2:
            reply = 'Send: present <id>'
        else:
            pid = parts[1]
            rows = db.list_attendance(date.today().isoformat())
            mine = next((r for r in rows if r['person_id'] == pid), None)
            if not mine:
                reply = f'No record today for #{pid}.'
            elif parts[0] == 'present':
                reply = (f'{mine.get("person_name") or pid} checked in at '
                         f'{mine.get("check_in") or "—"} '
                         f'({"late" if mine.get("is_late") else "on time"}).')
            else:
                reply = (f'{mine.get("person_name") or pid} '
                         f'check-out: {mine.get("check_out") or "not yet"}.')
    else:
        reply = 'Send "help" for commands.'

    notify.dispatch('whatsapp', from_, 'reply', reply, async_=True)
    return jsonify({'ok': True})


# ===========================================================================
# D1. Proxy-Proof Guarantee status page
# ===========================================================================
@app.route('/proxy-proof')
@login_required
def proxy_proof():
    """A single-pane "guard status" page — used as the sales headline."""
    s = db.get_all_settings()
    rows = [
        {'k': 'Liveness anti-spoofing (eye-blink + motion + texture)',
         'on': s.get('liveness_enabled', '1') == '1',
         'detail': 'Stops photo / video / mask replay attacks.'},
        {'k': 'Geofencing on mobile check-in',
         'on': bool(db.list_branches()),
         'detail': 'GPS marks are accepted only inside a defined site.'},
        {'k': 'Mock-GPS detection',
         'on': True,
         'detail': f'Rejects fixes with accuracy worse than '
                   f'{s.get("gps_accuracy_max_m", "60")} m, accuracy = 0, '
                   'or teleport >800 km/h.'},
        {'k': 'Encrypted biometric templates at rest',
         'on': s.get('encrypt_templates', '0') == '1',
         'detail': 'AES-256-GCM derived from FACEMARK_SECRET; no plaintext '
                   'face crops on disk once enabled.'},
        {'k': 'Crowd vote + cooldown',
         'on': True,
         'detail': 'Requires multiple confirming frames within a window before '
                   'marking; single-frame impersonations cannot cross the gate.'},
        {'k': 'Full audit log',
         'on': True,
         'detail': 'Every admin action stored for forensic review.'},
        {'k': 'Role-based access',
         'on': len(db.list_admins()) >= 1,
         'detail': 'admin / hr / teacher / staff roles enforced server-side.'},
        {'k': 'PIN / QR / RFID fallback',
         'on': True,
         'detail': 'Out-of-band identity if face fails (mask, injury, twin).'},
        {'k': 'PPE / helmet at gate (Site mode)',
         'on': s.get('site_mode_enabled', '0') == '1',
         'detail': 'Blocks attendance when helmet/vest missing.'},
        {'k': 'Temperature gate',
         'on': bool(s.get('temp_cutoff_c')),
         'detail': 'Fever-flag threshold blocks attendance + alerts guardian.'},
    ]
    score = sum(1 for r in rows if r['on'])
    return render_template('proxy_proof.html', rows=rows,
                           score=score, total=len(rows))


# ===========================================================================
# D6. Parent-meeting pack (Education Intelligence)
# ===========================================================================
@app.route('/parent-pack/<pid>')
@role_required('admin', 'hr', 'teacher')
def parent_pack(pid):
    """Single printable HTML pack: 90-day attendance, late tally, per-subject
    defaulter rows, at-risk note. Designed for PTA meetings."""
    person = db.get_person(pid)
    if not person:
        flash('Person not found.', 'warning')
        return redirect(url_for('list_users'))
    hist = db.person_history(pid, 90)
    present = sum(1 for r in hist if r['check_in'])
    late = sum(1 for r in hist if r['is_late'])
    pct = round(100 * present / max(1, len(hist)), 1)
    # per-subject report
    subject_rows = []
    for s in db.list_subjects():
        rpt = db.subject_attendance_report(s['id'])
        mine = next((r for r in rpt if r['person_id'] == pid), None)
        if mine and mine['held'] > 0:
            subject_rows.append({**mine, 'subject_name': s['name'],
                                 'subject_code': s['code']})
    risks = [r for r in db.at_risk_persons() if r['person_id'] == pid]
    return render_template('parent_pack.html',
                           person=dict(person),
                           rows=hist, present=present, late=late,
                           total_days=len(hist), pct=pct,
                           subject_rows=subject_rows,
                           risks=risks,
                           leaves=db.list_leaves(person_id=pid))


# ===========================================================================
# Bootstrap. This must run on *import*, not just under `python app.py`, so that
# WSGI servers (gunicorn app:app, as used by the Dockerfile) get a created
# schema and seeded admin. Without it every request 500s with
# "no such table: settings". Both calls are idempotent.
ensure_dirs()
db.init_db()


if __name__ == '__main__':
    # Background threads stay here: they do not survive gunicorn's fork() when
    # preload_app is on, and starting them per-worker would duplicate the
    # nightly jobs. See gunicorn post_fork if you need them in production.
    scheduler.start()
    ent_siem.start()
    app.run(debug=True)
