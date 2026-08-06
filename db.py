"""
SQLite database layer for FaceMark.

Tables
------
admin_users   – login credentials for the admin panel
departments   – classes / teams / departments
persons       – people whose faces are enrolled
attendance    – one row per (person, date) with check-in / check-out
settings      – key/value config (org name, late threshold, etc.)
audit_log     – every admin action
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from typing import Iterable, Optional

from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = 'facemark.db'

DEFAULT_SETTINGS = {
    'org_name': 'FaceMark Attendance',
    'work_start_time': '09:00',          # HH:MM
    'late_threshold_min': '15',          # minutes after start before "late"
    'recognition_threshold': '80',       # LBPH default: ~50-80 typical. For KNN fallback try 7000.
    'min_checkout_gap_min': '30',        # minimum minutes between check-in and check-out
    'logo_filename': '',                 # optional uploaded logo
    'attendance_required_pct': '75',     # college default: minimum % to pass
    'active_session_id': '',             # currently-running class session
}


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------
_PRAGMA_ONCE_DONE = False


def _pragma_once(conn: sqlite3.Connection) -> None:
    """Database-wide PRAGMAs that only need to be set the first time we touch
    the file in a process. WAL mode is persistent on disk so subsequent
    connections inherit it; busy_timeout is per-connection so it lives in the
    connect helper below.
    """
    global _PRAGMA_ONCE_DONE
    if _PRAGMA_ONCE_DONE:
        return
    try:
        conn.execute('PRAGMA journal_mode = WAL')      # concurrent readers
        conn.execute('PRAGMA synchronous = NORMAL')    # WAL-safe, much faster fsync
        conn.execute('PRAGMA temp_store = MEMORY')
        conn.execute('PRAGMA mmap_size = 268435456')   # 256 MB memory-mapped IO
        conn.execute('PRAGMA cache_size = -32000')     # ~32 MB page cache
        _PRAGMA_ONCE_DONE = True
    except sqlite3.OperationalError:
        # Read-only filesystem etc. — keep going with safe defaults
        _PRAGMA_ONCE_DONE = True


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10.0,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 5000')
    _pragma_once(conn)
    return conn


@contextmanager
def tx():
    """Short-lived transaction context."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema + seed
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departments (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS persons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id     TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    department_id INTEGER,
    email         TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL,
    date      TEXT NOT NULL,
    check_in  TEXT,
    check_out TEXT,
    is_late   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(person_id, date)
);
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

-- ── Academic model ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subjects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    code          TEXT,                 -- e.g. CS101
    department_id INTEGER,               -- which class/section it belongs to
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL,
    UNIQUE(name, department_id)
);

CREATE TABLE IF NOT EXISTS class_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id  INTEGER NOT NULL,
    date        TEXT NOT NULL,           -- YYYY-MM-DD
    start_time  TEXT NOT NULL,           -- HH:MM
    end_time    TEXT NOT NULL,           -- HH:MM
    notes       TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_date ON class_sessions(date);

CREATE TABLE IF NOT EXISTS session_attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,
    person_id   TEXT NOT NULL,
    marked_at   TEXT NOT NULL,
    UNIQUE(session_id, person_id),
    FOREIGN KEY(session_id) REFERENCES class_sessions(id) ON DELETE CASCADE
);

-- ── Visitor log (unknown faces seen at the kiosk) ─────────────────────────
CREATE TABLE IF NOT EXISTS visitors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot   TEXT NOT NULL,    -- relative path inside static/
    seen_at    TEXT NOT NULL,
    camera     TEXT
);
CREATE INDEX IF NOT EXISTS idx_visitors_seen ON visitors(seen_at DESC);

-- ── Branches / sites (multi-location) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS branches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT UNIQUE NOT NULL,
    address      TEXT,
    timezone     TEXT,
    -- geofence: either circle (lat/lng/radius_m) or polygon json
    lat          REAL,
    lng          REAL,
    radius_m     REAL,
    polygon_json TEXT,
    created_at   TEXT NOT NULL
);

-- ── Shifts ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shifts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    start_time    TEXT NOT NULL,             -- HH:MM
    end_time      TEXT NOT NULL,             -- HH:MM
    grace_min     INTEGER NOT NULL DEFAULT 10,
    branch_id     INTEGER,
    department_id INTEGER,
    days_mask     TEXT NOT NULL DEFAULT '1111100',  -- Mon..Sun
    FOREIGN KEY(branch_id)     REFERENCES branches(id)    ON DELETE SET NULL,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS person_shift (
    person_id  TEXT NOT NULL,
    shift_id   INTEGER NOT NULL,
    effective  TEXT NOT NULL,   -- YYYY-MM-DD
    PRIMARY KEY (person_id, shift_id, effective)
);

-- ── Leaves ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leave_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT NOT NULL,
    leave_type  TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    reason      TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/rejected
    decided_by  TEXT,
    decided_at  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leave_person ON leave_requests(person_id);
CREATE INDEX IF NOT EXISTS idx_leave_dates  ON leave_requests(start_date, end_date);

-- ── Timetable (recurring weekly periods) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS timetable (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id   INTEGER NOT NULL,
    weekday      INTEGER NOT NULL,   -- 0=Mon..6=Sun
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    room         TEXT,
    teacher      TEXT,
    active_from  TEXT,                -- optional date window
    active_to    TEXT,
    FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE
);

-- ── Holiday / academic calendar ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS holidays (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    name        TEXT NOT NULL,
    branch_id   INTEGER,              -- null = global
    FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE CASCADE,
    UNIQUE(date, branch_id)
);

-- ── Consent (DPDP / GDPR) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS consents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id    TEXT NOT NULL,
    purpose      TEXT NOT NULL,        -- 'biometric', 'notifications', etc.
    granted      INTEGER NOT NULL,     -- 0 / 1
    granted_at   TEXT NOT NULL,
    revoked_at   TEXT,
    proof_text   TEXT,                 -- snapshot of consent wording shown
    proof_sig    TEXT,                 -- HMAC of the proof for tamper-evidence
    UNIQUE(person_id, purpose)
);

CREATE TABLE IF NOT EXISTS erasure_requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id  TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    fulfilled_at TEXT,
    actor      TEXT
);

-- ── Notifications log ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    channel   TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject   TEXT,
    body      TEXT,
    status    TEXT NOT NULL,
    detail    TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at DESC);

-- ── API keys + webhooks ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_keys (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    label     TEXT NOT NULL,
    key_hash  TEXT NOT NULL,
    scopes    TEXT,                  -- csv: read,write,attendance,users,...
    created_at TEXT NOT NULL,
    last_used TEXT,
    revoked   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS webhooks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    url       TEXT NOT NULL,
    events    TEXT NOT NULL,         -- csv: check_in,check_out,absent,visitor
    secret    TEXT,
    enabled   INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_status TEXT
);

-- ── GPS / geofence check-in log ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gps_marks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT NOT NULL,
    branch_id   INTEGER,
    lat         REAL NOT NULL,
    lng         REAL NOT NULL,
    accuracy_m  REAL,
    status      TEXT NOT NULL,         -- accepted / rejected / mocked
    reason      TEXT,
    created_at  TEXT NOT NULL
);

-- ── Contractors + workers (Construction Site Edition) ────────────────────
CREATE TABLE IF NOT EXISTS contractors (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT UNIQUE NOT NULL,
    contact   TEXT,
    site_id   INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(site_id) REFERENCES branches(id) ON DELETE SET NULL
);

-- ── PPE / safety incidents at gate ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS ppe_incidents (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT,
    branch_id INTEGER,
    detected  TEXT NOT NULL,    -- csv of detected items
    missing   TEXT NOT NULL,    -- csv of missing required items
    snapshot  TEXT,
    seen_at   TEXT NOT NULL
);

-- ── Daily-wage muster (Site SKU) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS site_muster (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id    TEXT NOT NULL,
    branch_id    INTEGER,
    contractor_id INTEGER,
    date         TEXT NOT NULL,
    hours        REAL NOT NULL DEFAULT 0,
    daily_rate   REAL NOT NULL DEFAULT 0,
    overtime_hr  REAL NOT NULL DEFAULT 0,
    note         TEXT,
    UNIQUE(person_id, date)
);

-- ── Multi-camera registry (RTSP / HTTP / USB index) ──────────────────────
CREATE TABLE IF NOT EXISTS cameras (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    url         TEXT NOT NULL,           -- rtsp://… or http://… or '0'/'1' for USB index
    branch_id   INTEGER,
    purpose     TEXT NOT NULL DEFAULT 'attendance',   -- 'attendance' / 'door' / 'site_gate' / 'exam'
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE SET NULL
);

-- ── Transport / school bus ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bus_routes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    driver      TEXT,
    vehicle_no  TEXT,
    branch_id   INTEGER,
    created_at  TEXT NOT NULL,
    FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS bus_stops (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id    INTEGER NOT NULL,
    name        TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    lat         REAL, lng REAL,
    FOREIGN KEY(route_id) REFERENCES bus_routes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bus_assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT NOT NULL,
    route_id    INTEGER NOT NULL,
    stop_id     INTEGER,
    UNIQUE(person_id, route_id)
);

CREATE TABLE IF NOT EXISTS bus_boardings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT NOT NULL,
    route_id    INTEGER,
    stop_id     INTEGER,
    direction   TEXT NOT NULL,           -- 'board' / 'alight'
    seen_at     TEXT NOT NULL,
    location    TEXT                     -- 'lat,lng' optional
);
CREATE INDEX IF NOT EXISTS idx_boardings_when ON bus_boardings(seen_at DESC);

-- ── Substitute / proxy-teacher handling ──────────────────────────────────
CREATE TABLE IF NOT EXISTS substitutions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL,
    original     TEXT,
    substitute   TEXT NOT NULL,
    note         TEXT,
    created_at   TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES class_sessions(id) ON DELETE CASCADE
);

-- ── Sensor readings (temperature, mask-detected, etc.) ───────────────────
CREATE TABLE IF NOT EXISTS sensor_readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT,
    branch_id   INTEGER,
    kind        TEXT NOT NULL,           -- 'temperature_c' / 'mask' / 'co2_ppm'
    value_num   REAL,
    value_text  TEXT,
    seen_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sensor_when ON sensor_readings(seen_at DESC);

-- ── Door / turnstile relay log ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS door_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id   INTEGER,
    person_id   TEXT,
    relay_url   TEXT,
    http_status TEXT,
    seen_at     TEXT NOT NULL
);

-- ── Exam / continuous-presence verification ──────────────────────────────
CREATE TABLE IF NOT EXISTS exam_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    branch_id   INTEGER,
    department_id INTEGER,
    start_at    TEXT NOT NULL,
    end_at      TEXT NOT NULL,
    check_every_sec INTEGER NOT NULL DEFAULT 60,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exam_alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id      INTEGER NOT NULL,
    person_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,    -- 'missing' / 'imposter' / 'phone'
    detail       TEXT,
    seen_at      TEXT NOT NULL,
    FOREIGN KEY(exam_id) REFERENCES exam_sessions(id) ON DELETE CASCADE
);

-- ── Cloud backup log ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS backup_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    destination TEXT NOT NULL,
    bytes       INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);

-- ═══════════════════════════════════════════════════════════════════════
-- ENTERPRISE EDITION (N1-N20)
-- ═══════════════════════════════════════════════════════════════════════

-- N17 ── Tenants (multi-tenant control plane) ────────────────────────────
CREATE TABLE IF NOT EXISTS tenants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT UNIQUE NOT NULL,        -- 'acme' -> acme.facemark.example
    name        TEXT NOT NULL,
    plan        TEXT NOT NULL DEFAULT 'starter',  -- starter/pro/enterprise/msp
    brand_color TEXT,
    brand_logo  TEXT,
    parent_id   INTEGER,                      -- for reseller hierarchy (N18)
    seats       INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    FOREIGN KEY(parent_id) REFERENCES tenants(id) ON DELETE SET NULL
);

-- N1 ── SSO / SAML / OIDC providers ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS sso_providers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     INTEGER,
    kind          TEXT NOT NULL,             -- 'oidc' / 'saml' / 'google' / 'entra' / 'okta'
    name          TEXT NOT NULL,
    issuer        TEXT,
    client_id     TEXT,
    client_secret TEXT,
    auth_url      TEXT,
    token_url     TEXT,
    userinfo_url  TEXT,
    metadata_url  TEXT,                       -- saml IdP metadata
    domain        TEXT,                       -- restrict to email domain
    default_role  TEXT NOT NULL DEFAULT 'staff',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

-- N2 ── SCIM clients (provisioning) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS scim_clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER,
    name        TEXT NOT NULL,
    bearer_hash TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_seen   TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

-- N3 ── SIEM audit sinks (Splunk / Datadog) ──────────────────────────────
CREATE TABLE IF NOT EXISTS siem_sinks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    auth_header TEXT,
    fmt         TEXT NOT NULL DEFAULT 'json',     -- json/cef/leef
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_status TEXT,
    last_sent   TEXT,
    created_at  TEXT NOT NULL
);

-- N3 ── extended audit with retention category
CREATE TABLE IF NOT EXISTS audit_extended (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category     TEXT NOT NULL,                   -- 'auth' / 'admin' / 'impersonation' / 'mfa'
    actor        TEXT,
    target       TEXT,
    action       TEXT NOT NULL,
    detail       TEXT,
    ip           TEXT,
    user_agent   TEXT,
    streamed     INTEGER NOT NULL DEFAULT 0,      -- pushed to SIEM yet?
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ext_when ON audit_extended(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_ext_cat  ON audit_extended(category);

-- N5 ── Deepfake / virtual-camera detections
CREATE TABLE IF NOT EXISTS spoof_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT,
    kind        TEXT NOT NULL,                   -- 'virtual_camera' / 'replay' / 'deepfake' / 'inject'
    score       REAL,
    snapshot    TEXT,
    detail      TEXT,
    seen_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spoof_when ON spoof_events(seen_at DESC);

-- N6 ── Tailgating + anti-passback log
CREATE TABLE IF NOT EXISTS access_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id   INTEGER,
    person_id   TEXT,
    kind        TEXT NOT NULL,                   -- 'tailgate' / 'antipassback' / 'unknown_extra'
    face_count  INTEGER,
    direction   TEXT,                            -- 'in' / 'out'
    snapshot    TEXT,
    detail      TEXT,
    seen_at     TEXT NOT NULL
);

-- N7 ── BYO-KMS key wraps
CREATE TABLE IF NOT EXISTS kms_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    INTEGER,
    label        TEXT NOT NULL,
    kms_kind     TEXT NOT NULL,                  -- 'aws' / 'gcp' / 'azure' / 'hashicorp' / 'static'
    key_ref      TEXT NOT NULL,                  -- ARN / KeyVault URI / etc.
    wrapped_dek  BLOB,                           -- wrapped data encryption key
    rotated_at   TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

-- N9 ── Teams / Slack check-ins
CREATE TABLE IF NOT EXISTS chat_checkins (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel      TEXT NOT NULL,                  -- 'slack' / 'teams'
    workspace    TEXT,
    chat_user_id TEXT NOT NULL,
    person_id    TEXT,
    event        TEXT NOT NULL,                  -- 'check_in' / 'check_out' / 'wfh'
    selfie       TEXT,
    seen_at      TEXT NOT NULL
);

-- N10 ── Presence signals (Wi-Fi/VPN/calendar)
CREATE TABLE IF NOT EXISTS presence_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT NOT NULL,
    source      TEXT NOT NULL,                   -- 'wifi' / 'vpn' / 'calendar'
    detail      TEXT,
    score       REAL NOT NULL DEFAULT 1.0,
    seen_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_presence_person ON presence_signals(person_id, seen_at DESC);

-- N12 ── Approval workflows
CREATE TABLE IF NOT EXISTS workflows (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    trigger      TEXT NOT NULL,                  -- 'leave' / 'overtime' / 'wfh' / 'comp_off' / 'regularisation'
    steps_json   TEXT NOT NULL,                  -- [{"role":"manager"},{"role":"hr"},{"approver":"admin"}]
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id  INTEGER NOT NULL,
    subject      TEXT NOT NULL,                  -- target object e.g. 'leave:42'
    requester    TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'pending', -- pending/approved/rejected
    step_idx     INTEGER NOT NULL DEFAULT 0,
    decisions_json TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    closed_at    TEXT,
    FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

-- N13 ── AI Copilot question log
CREATE TABLE IF NOT EXISTS copilot_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT,
    question    TEXT NOT NULL,
    intent      TEXT,
    sql_run     TEXT,
    rows_out    INTEGER,
    success     INTEGER NOT NULL DEFAULT 1,
    answered_at TEXT NOT NULL
);

-- N15 ── HRMS / payroll connectors
CREATE TABLE IF NOT EXISTS connectors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                   -- 'sap_sf' / 'workday' / 'adp' / 'bamboo' / 'zoho_people' / 'keka' / 'greythr' / 'zapier'
    name        TEXT NOT NULL,
    endpoint    TEXT,
    api_key     TEXT,
    api_secret  TEXT,
    config_json TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_sync   TEXT,
    last_status TEXT,
    created_at  TEXT NOT NULL
);

-- N16 ── Space / desk occupancy
CREATE TABLE IF NOT EXISTS rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id   INTEGER,
    name        TEXT NOT NULL,
    capacity    INTEGER NOT NULL DEFAULT 0,
    kind        TEXT,                            -- 'meeting' / 'desk' / 'floor'
    UNIQUE(branch_id, name)
);

CREATE TABLE IF NOT EXISTS occupancy_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     INTEGER NOT NULL,
    head_count  INTEGER NOT NULL,
    seen_at     TEXT NOT NULL,
    FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_occupancy_when ON occupancy_samples(seen_at DESC);

-- N8 ── Emergency mustering
CREATE TABLE IF NOT EXISTS muster_drills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id    INTEGER,
    name         TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    safe_zone    TEXT
);

CREATE TABLE IF NOT EXISTS muster_checkins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drill_id    INTEGER NOT NULL,
    person_id   TEXT NOT NULL,
    seen_at     TEXT NOT NULL,
    UNIQUE(drill_id, person_id),
    FOREIGN KEY(drill_id) REFERENCES muster_drills(id) ON DELETE CASCADE
);

-- N19 ── Face-auth SDK clients
CREATE TABLE IF NOT EXISTS sdk_clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER,
    name        TEXT NOT NULL,
    public_id   TEXT UNIQUE NOT NULL,
    secret_hash TEXT NOT NULL,
    origins     TEXT,                            -- csv of allowed origins
    rate_per_min INTEGER NOT NULL DEFAULT 60,
    created_at  TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sdk_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL,
    endpoint    TEXT NOT NULL,
    status      TEXT NOT NULL,
    ms          INTEGER,
    seen_at     TEXT NOT NULL
);
"""

# Indexes live in a separate script because several of them target columns that
# are added by the ALTER TABLE migrations in init_db() (persons.branch_id,
# persons.rfid_uid, admin_users.sso_subject, ...). Executing them as part of
# SCHEMA works only on a database that has already been migrated once — on a
# genuinely empty file it fails with "no such column: branch_id". init_db()
# therefore runs SCHEMA, then the migrations, then SCHEMA_INDEXES.
SCHEMA_INDEXES = """
-- Indexes added in the optimization pass
CREATE INDEX IF NOT EXISTS idx_attendance_person      ON attendance(person_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_attendance_late        ON attendance(date, is_late);
CREATE INDEX IF NOT EXISTS idx_session_att_person     ON session_attendance(person_id);
CREATE INDEX IF NOT EXISTS idx_leaves_status          ON leave_requests(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notif_recipient        ON notifications(recipient);
CREATE INDEX IF NOT EXISTS idx_gps_person             ON gps_marks(person_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_persons_branch         ON persons(branch_id);
CREATE INDEX IF NOT EXISTS idx_persons_dept           ON persons(department_id);
CREATE INDEX IF NOT EXISTS idx_persons_chat_user      ON persons(chat_user_id);
CREATE INDEX IF NOT EXISTS idx_persons_rfid           ON persons(rfid_uid);
CREATE INDEX IF NOT EXISTS idx_persons_scim_ext       ON persons(scim_external_id);
CREATE INDEX IF NOT EXISTS idx_audit_ext_streamed     ON audit_extended(streamed, id);
CREATE INDEX IF NOT EXISTS idx_chat_user              ON chat_checkins(chat_user_id, seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_state    ON workflow_runs(state, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_muster_drill_open      ON muster_drills(ended_at);
CREATE INDEX IF NOT EXISTS idx_door_log_when          ON door_log(seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_sdk_calls_client_when  ON sdk_calls(client_id, seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_sso              ON admin_users(sso_subject, sso_provider);
"""


def init_db(default_admin: tuple[str, str] | None = ('admin', 'admin123')) -> None:
    """Create tables, seed defaults, run lightweight migrations.
    Safe to call repeatedly."""
    with tx() as c:
        c.executescript(SCHEMA)

        # Migration: add guardian_email + date_of_birth + extended fields
        cols = [r['name'] for r in c.execute('PRAGMA table_info(persons)').fetchall()]
        for col, ddl in (
            ('guardian_email', 'TEXT'),
            ('date_of_birth',  'TEXT'),
            ('phone',          'TEXT'),
            ('guardian_phone', 'TEXT'),
            ('branch_id',      'INTEGER'),
            ('contractor_id',  'INTEGER'),
            ('pin_hash',       'TEXT'),
            ('qr_secret',      'TEXT'),
            ('lang',           'TEXT'),
            ('rfid_uid',       'TEXT'),
            ('tenant_id',      'INTEGER'),       # N17
            ('manager_id',     'TEXT'),          # N12 workflow routing
            ('chat_user_id',   'TEXT'),          # N9 maps Slack/Teams uid
            ('scim_external_id','TEXT'),         # N2
        ):
            if col not in cols:
                c.execute(f'ALTER TABLE persons ADD COLUMN {col} {ddl}')

        # admin_users role flexibility
        au_cols = [r['name'] for r in c.execute('PRAGMA table_info(admin_users)').fetchall()]
        for col, ddl in (
            ('branch_id',     'INTEGER'),
            ('email',         'TEXT'),
            ('tenant_id',     'INTEGER'),        # N17
            ('sso_subject',   'TEXT'),           # N1 (idP user id)
            ('sso_provider',  'INTEGER'),        # N1 (FK sso_providers)
            ('mfa_secret',    'TEXT'),
        ):
            if col not in au_cols:
                c.execute(f'ALTER TABLE admin_users ADD COLUMN {col} {ddl}')

        # Indexes: must come after the migrations above, since several of them
        # target columns those migrations add.
        c.executescript(SCHEMA_INDEXES)

        # Seed settings
        for k, v in DEFAULT_SETTINGS.items():
            c.execute('INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)', (k, v))

        # Seed default admin (only if none exist)
        if default_admin:
            n = c.execute('SELECT COUNT(*) FROM admin_users').fetchone()[0]
            if n == 0:
                u, p = default_admin
                c.execute(
                    'INSERT INTO admin_users(username, password_hash, role, created_at) VALUES (?, ?, ?, ?)',
                    (u, generate_password_hash(p), 'admin', datetime.now().isoformat(timespec='seconds')),
                )

        # Seed a couple of starter departments
        for d in ('General', 'Engineering', 'Operations'):
            c.execute('INSERT OR IGNORE INTO departments(name) VALUES (?)', (d,))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with tx() as c:
        r = c.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return r['value'] if r else default


def get_all_settings() -> dict:
    with tx() as c:
        rows = c.execute('SELECT key, value FROM settings').fetchall()
        return {r['key']: r['value'] for r in rows}


def set_setting(key: str, value: str) -> None:
    with tx() as c:
        c.execute(
            'INSERT INTO settings(key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, value),
        )


# ---------------------------------------------------------------------------
# Admin users
# ---------------------------------------------------------------------------
def verify_admin(username: str, password: str) -> Optional[sqlite3.Row]:
    with tx() as c:
        r = c.execute('SELECT * FROM admin_users WHERE username = ?', (username,)).fetchone()
        if r and check_password_hash(r['password_hash'], password):
            return r
    return None


def change_admin_password(username: str, new_password: str) -> bool:
    with tx() as c:
        cur = c.execute(
            'UPDATE admin_users SET password_hash = ? WHERE username = ?',
            (generate_password_hash(new_password), username),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
def list_departments() -> list[sqlite3.Row]:
    with tx() as c:
        return c.execute('SELECT * FROM departments ORDER BY name').fetchall()


def add_department(name: str) -> None:
    with tx() as c:
        c.execute('INSERT OR IGNORE INTO departments(name) VALUES (?)', (name.strip(),))


def delete_department(dept_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM departments WHERE id = ?', (dept_id,))


# ---------------------------------------------------------------------------
# Persons
# ---------------------------------------------------------------------------
def upsert_person(person_id: str, name: str, department_id: Optional[int] = None,
                  email: Optional[str] = None,
                  guardian_email: Optional[str] = None,
                  date_of_birth: Optional[str] = None) -> None:
    with tx() as c:
        c.execute(
            'INSERT INTO persons(person_id, name, department_id, email, '
            '                    guardian_email, date_of_birth, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(person_id) DO UPDATE SET '
            '  name = excluded.name, '
            '  department_id = excluded.department_id, '
            '  email = excluded.email, '
            '  guardian_email = excluded.guardian_email, '
            '  date_of_birth = excluded.date_of_birth',
            (person_id, name, department_id, email, guardian_email,
             date_of_birth, datetime.now().isoformat(timespec='seconds')),
        )


def get_person(person_id: str) -> Optional[sqlite3.Row]:
    with tx() as c:
        return c.execute(
            'SELECT p.*, d.name AS department_name '
            'FROM persons p LEFT JOIN departments d ON d.id = p.department_id '
            'WHERE p.person_id = ?',
            (person_id,),
        ).fetchone()


def list_persons(department_id: Optional[int] = None, search: str = '') -> list[sqlite3.Row]:
    q = ('SELECT p.*, d.name AS department_name '
         'FROM persons p LEFT JOIN departments d ON d.id = p.department_id WHERE 1=1 ')
    args: list = []
    if department_id:
        q += ' AND p.department_id = ?'; args.append(department_id)
    if search:
        q += ' AND (p.name LIKE ? OR p.person_id LIKE ?)'
        like = f'%{search}%'; args += [like, like]
    q += ' ORDER BY p.name'
    with tx() as c:
        return c.execute(q, args).fetchall()


def delete_person(person_id: str) -> None:
    with tx() as c:
        c.execute('DELETE FROM persons WHERE person_id = ?', (person_id,))


def count_persons() -> int:
    with tx() as c:
        return c.execute('SELECT COUNT(*) FROM persons').fetchone()[0]


def update_person(person_id: str, name: Optional[str] = None,
                  department_id: Optional[int] = None,
                  email: Optional[str] = None,
                  guardian_email: Optional[str] = None,
                  date_of_birth: Optional[str] = None) -> None:
    fields, args = [], []
    if name is not None:
        fields.append('name = ?'); args.append(name)
    if department_id is not None:
        fields.append('department_id = ?'); args.append(department_id or None)
    if email is not None:
        fields.append('email = ?'); args.append(email or None)
    if guardian_email is not None:
        fields.append('guardian_email = ?'); args.append(guardian_email or None)
    if date_of_birth is not None:
        fields.append('date_of_birth = ?'); args.append(date_of_birth or None)
    if not fields:
        return
    args.append(person_id)
    with tx() as c:
        c.execute(f'UPDATE persons SET {", ".join(fields)} WHERE person_id = ?', args)


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
def list_subjects(department_id: Optional[int] = None) -> list[sqlite3.Row]:
    q = ('SELECT s.*, d.name AS department_name '
         'FROM subjects s LEFT JOIN departments d ON d.id = s.department_id ')
    args: list = []
    if department_id:
        q += ' WHERE s.department_id = ?'
        args.append(department_id)
    q += ' ORDER BY s.name'
    with tx() as c:
        return c.execute(q, args).fetchall()


def add_subject(name: str, code: str = '', department_id: Optional[int] = None) -> None:
    with tx() as c:
        c.execute(
            'INSERT OR IGNORE INTO subjects(name, code, department_id) VALUES (?, ?, ?)',
            (name.strip(), code.strip() or None, department_id),
        )


def delete_subject(subject_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM subjects WHERE id = ?', (subject_id,))


def get_subject(subject_id: int) -> Optional[sqlite3.Row]:
    with tx() as c:
        return c.execute(
            'SELECT s.*, d.name AS department_name '
            'FROM subjects s LEFT JOIN departments d ON d.id = s.department_id '
            'WHERE s.id = ?', (subject_id,)
        ).fetchone()


# ---------------------------------------------------------------------------
# Class sessions
# ---------------------------------------------------------------------------
def create_session(subject_id: int, date_iso: str,
                   start_time: str, end_time: str, notes: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO class_sessions(subject_id, date, start_time, end_time, notes, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (subject_id, date_iso, start_time, end_time, notes or None,
             datetime.now().isoformat(timespec='seconds')),
        )
        return cur.lastrowid


def list_sessions(date_iso: Optional[str] = None,
                  subject_id: Optional[int] = None) -> list[dict]:
    q = ('SELECT cs.*, s.name AS subject_name, s.code AS subject_code, '
         '       d.name AS department_name, '
         '       (SELECT COUNT(*) FROM session_attendance sa WHERE sa.session_id = cs.id) AS present '
         'FROM class_sessions cs '
         'LEFT JOIN subjects s ON s.id = cs.subject_id '
         'LEFT JOIN departments d ON d.id = s.department_id '
         'WHERE 1=1 ')
    args: list = []
    if date_iso:
        q += ' AND cs.date = ?'; args.append(date_iso)
    if subject_id:
        q += ' AND cs.subject_id = ?'; args.append(subject_id)
    q += ' ORDER BY cs.date DESC, cs.start_time DESC'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def get_session(session_id: int) -> Optional[dict]:
    with tx() as c:
        r = c.execute(
            'SELECT cs.*, s.name AS subject_name, s.code AS subject_code, '
            '       s.department_id AS subject_dept_id, '
            '       d.name AS department_name '
            'FROM class_sessions cs '
            'LEFT JOIN subjects s ON s.id = cs.subject_id '
            'LEFT JOIN departments d ON d.id = s.department_id '
            'WHERE cs.id = ?', (session_id,)).fetchone()
    return dict(r) if r else None


def delete_session(session_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM class_sessions WHERE id = ?', (session_id,))


def session_present(session_id: int) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT sa.*, p.name AS person_name, p.department_id, d.name AS department_name '
            'FROM session_attendance sa '
            'LEFT JOIN persons p ON p.person_id = sa.person_id '
            'LEFT JOIN departments d ON d.id = p.department_id '
            'WHERE sa.session_id = ? ORDER BY sa.marked_at',
            (session_id,)).fetchall()]


def mark_session_attendance(session_id: int, person_id: str) -> bool:
    """Returns True if newly marked, False if already there."""
    now = datetime.now().strftime('%H:%M:%S')
    with tx() as c:
        cur = c.execute(
            'INSERT OR IGNORE INTO session_attendance(session_id, person_id, marked_at) '
            'VALUES (?, ?, ?)',
            (session_id, person_id, now),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Attendance percentage / defaulters (the "75% rule")
# ---------------------------------------------------------------------------
def subject_attendance_report(subject_id: int) -> list[dict]:
    """For each person in the subject's department, return:
       { person_id, name, attended, held, pct }
    """
    with tx() as c:
        sub = c.execute('SELECT * FROM subjects WHERE id = ?', (subject_id,)).fetchone()
        if sub is None:
            return []
        held = c.execute(
            'SELECT COUNT(*) FROM class_sessions WHERE subject_id = ?', (subject_id,)
        ).fetchone()[0]

        if sub['department_id']:
            people = c.execute(
                'SELECT * FROM persons WHERE department_id = ? ORDER BY name',
                (sub['department_id'],),
            ).fetchall()
        else:
            people = c.execute('SELECT * FROM persons ORDER BY name').fetchall()

        out = []
        for p in people:
            attended = c.execute(
                'SELECT COUNT(*) FROM session_attendance sa '
                'JOIN class_sessions cs ON cs.id = sa.session_id '
                'WHERE cs.subject_id = ? AND sa.person_id = ?',
                (subject_id, p['person_id']),
            ).fetchone()[0]
            pct = round((attended / held) * 100, 1) if held else 0.0
            out.append({
                'person_id': p['person_id'],
                'name': p['name'],
                'attended': attended,
                'held': held,
                'pct': pct,
            })
        return out


def all_defaulters(min_pct: float) -> list[dict]:
    """All (subject, person) pairs below min_pct."""
    rows = []
    for s in list_subjects():
        for r in subject_attendance_report(s['id']):
            if r['held'] > 0 and r['pct'] < min_pct:
                r2 = dict(r)
                r2['subject_id']   = s['id']
                r2['subject_name'] = s['name']
                r2['subject_code'] = s['code']
                rows.append(r2)
    rows.sort(key=lambda x: (x['pct'], x['name']))
    return rows


def yet_to_arrive(department_id: Optional[int] = None) -> list[dict]:
    """Registered persons with no check-in row for today."""
    today = _today_iso()
    q = ('SELECT p.person_id, p.name, d.name AS department_name '
         'FROM persons p LEFT JOIN departments d ON d.id = p.department_id '
         'WHERE NOT EXISTS ('
         '  SELECT 1 FROM attendance a '
         '  WHERE a.person_id = p.person_id AND a.date = ? AND a.check_in IS NOT NULL'
         ') ')
    args: list = [today]
    if department_id:
        q += ' AND p.department_id = ?'; args.append(department_id)
    q += ' ORDER BY p.name'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def recent_sightings(limit: int = 10) -> list[dict]:
    """Most recent attendance events for the live feed."""
    today = _today_iso()
    with tx() as c:
        rows = c.execute(
            'SELECT a.person_id, a.check_in, a.check_out, a.is_late, p.name AS person_name '
            'FROM attendance a LEFT JOIN persons p ON p.person_id = a.person_id '
            "WHERE a.date = ? "
            'ORDER BY COALESCE(a.check_out, a.check_in) DESC LIMIT ?',
            (today, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def person_history(person_id: str, days: int = 30) -> list[dict]:
    with tx() as c:
        rows = c.execute(
            'SELECT date, check_in, check_out, is_late '
            'FROM attendance WHERE person_id = ? '
            "AND date >= date('now', ?) "
            'ORDER BY date DESC',
            (person_id, f'-{days - 1} days'),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
def _today_iso() -> str:
    return date.today().isoformat()


def manual_check_in(person_id: str, work_start: str, late_min: int) -> dict:
    """Force a check-in row regardless of camera state."""
    return mark_attendance(person_id, work_start, late_min, min_gap_min=0)


def manual_check_out(person_id: str) -> dict:
    """Force a check-out time on today's row."""
    now = datetime.now().strftime('%H:%M:%S')
    today = _today_iso()
    with tx() as c:
        c.execute(
            'INSERT INTO attendance(person_id, date, check_in, check_out) '
            'VALUES (?, ?, ?, ?) '
            'ON CONFLICT(person_id, date) DO UPDATE SET check_out = excluded.check_out',
            (person_id, today, now, now),
        )
    return {'event': 'manual_check_out', 'time': now}


def mark_attendance(person_id: str, work_start: str, late_min: int, min_gap_min: int) -> dict:
    """
    Idempotent: first call records check-in. Subsequent calls within the same
    day update check_out (after min_gap_min minutes). Returns a small status dict.
    """
    now = datetime.now()
    today = now.date().isoformat()
    nowt = now.strftime('%H:%M:%S')

    with tx() as c:
        row = c.execute(
            'SELECT * FROM attendance WHERE person_id = ? AND date = ?',
            (person_id, today),
        ).fetchone()

        if row is None:
            # First sighting today = check-in
            try:
                hh, mm = map(int, work_start.split(':'))
                cutoff = now.replace(hour=hh, minute=mm + late_min, second=0, microsecond=0)
                is_late = 1 if now > cutoff else 0
            except Exception:
                is_late = 0
            c.execute(
                'INSERT INTO attendance(person_id, date, check_in, is_late) VALUES (?, ?, ?, ?)',
                (person_id, today, nowt, is_late),
            )
            return {'event': 'check_in', 'time': nowt, 'is_late': bool(is_late)}

        # Already checked in — maybe update check-out
        if row['check_in']:
            try:
                ci = datetime.strptime(f"{today} {row['check_in']}", '%Y-%m-%d %H:%M:%S')
                if (now - ci).total_seconds() < min_gap_min * 60:
                    return {'event': 'already_in', 'time': row['check_in']}
            except Exception:
                pass
        c.execute(
            'UPDATE attendance SET check_out = ? WHERE person_id = ? AND date = ?',
            (nowt, person_id, today),
        )
        return {'event': 'check_out', 'time': nowt}


def list_attendance(date_iso: Optional[str] = None) -> list[dict]:
    date_iso = date_iso or _today_iso()
    with tx() as c:
        rows = c.execute(
            'SELECT a.*, p.name AS person_name, d.name AS department_name '
            'FROM attendance a '
            'LEFT JOIN persons p ON p.person_id = a.person_id '
            'LEFT JOIN departments d ON d.id = p.department_id '
            'WHERE a.date = ? ORDER BY a.check_in',
            (date_iso,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_attendance_dates() -> list[str]:
    with tx() as c:
        return [r['date'] for r in c.execute(
            'SELECT DISTINCT date FROM attendance ORDER BY date DESC'
        ).fetchall()]


def attendance_summary(days: int = 7) -> dict:
    """Returns per-day counts for the last `days` days."""
    with tx() as c:
        rows = c.execute(
            'SELECT date, '
            '       COUNT(*) AS total, '
            '       SUM(CASE WHEN is_late = 1 THEN 1 ELSE 0 END) AS late '
            'FROM attendance '
            "WHERE date >= date('now', ?) "
            'GROUP BY date ORDER BY date',
            (f'-{days - 1} days',),
        ).fetchall()
    return {'rows': [dict(r) for r in rows]}


def top_attenders(days: int = 30, limit: int = 5) -> list[dict]:
    with tx() as c:
        rows = c.execute(
            'SELECT p.person_id, p.name, COUNT(*) AS days_present '
            'FROM attendance a JOIN persons p ON p.person_id = a.person_id '
            "WHERE a.date >= date('now', ?) "
            'GROUP BY p.person_id, p.name '
            'ORDER BY days_present DESC LIMIT ?',
            (f'-{days - 1} days', limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def audit(actor: Optional[str], action: str, detail: str = '') -> None:
    with tx() as c:
        c.execute(
            'INSERT INTO audit_log(actor, action, detail, created_at) VALUES (?, ?, ?, ?)',
            (actor, action, detail, datetime.now().isoformat(timespec='seconds')),
        )


def list_audit(limit: int = 100) -> list[dict]:
    with tx() as c:
        rows = c.execute(
            'SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?', (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Visitor log
# ---------------------------------------------------------------------------
def log_visitor(snapshot_path: str, camera: str = '') -> None:
    with tx() as c:
        c.execute(
            'INSERT INTO visitors(snapshot, seen_at, camera) VALUES (?, ?, ?)',
            (snapshot_path,
             datetime.now().isoformat(timespec='seconds'),
             camera or None),
        )


def list_visitors(limit: int = 100) -> list[dict]:
    with tx() as c:
        rows = c.execute(
            'SELECT * FROM visitors ORDER BY seen_at DESC LIMIT ?', (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def clear_visitors() -> None:
    with tx() as c:
        c.execute('DELETE FROM visitors')


# ---------------------------------------------------------------------------
# Birthdays (today)
# ---------------------------------------------------------------------------
def birthdays_today() -> list[dict]:
    today = date.today().strftime('%m-%d')
    with tx() as c:
        rows = c.execute(
            "SELECT p.*, d.name AS department_name "
            "FROM persons p LEFT JOIN departments d ON d.id = p.department_id "
            "WHERE p.date_of_birth IS NOT NULL "
            "  AND substr(p.date_of_birth, 6, 5) = ? "
            "ORDER BY p.name", (today,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Smart insights — auto-generated dashboard alerts
# ---------------------------------------------------------------------------
def smart_insights() -> list[dict]:
    """Returns a small list of human-readable alerts. Cheap SQL only."""
    out = []
    today = _today_iso()

    with tx() as c:
        # — How many late today
        late = c.execute(
            "SELECT COUNT(*) FROM attendance WHERE date = ? AND is_late = 1",
            (today,),
        ).fetchone()[0]
        if late > 0:
            out.append({
                'kind': 'warn', 'icon': 'schedule',
                'title': f'{late} late arrival{"s" if late != 1 else ""} today',
                'cta': 'View today',
                'href': '/',
            })

        # — How many absent (registered but no check-in)
        absent = c.execute(
            "SELECT COUNT(*) FROM persons p "
            "WHERE NOT EXISTS (SELECT 1 FROM attendance a "
            "                  WHERE a.person_id = p.person_id "
            "                  AND a.date = ? AND a.check_in IS NOT NULL)",
            (today,),
        ).fetchone()[0]
        if absent > 0:
            out.append({
                'kind': 'info', 'icon': 'event_busy',
                'title': f'{absent} not yet arrived',
                'cta': 'Check yet-to-arrive',
                'href': '/',
            })

        # — Recent visitors (last 24h)
        unknown = c.execute(
            "SELECT COUNT(*) FROM visitors "
            "WHERE seen_at >= datetime('now', '-1 day')"
        ).fetchone()[0]
        if unknown > 0:
            out.append({
                'kind': 'danger', 'icon': 'no_accounts',
                'title': f'{unknown} unknown face{"s" if unknown != 1 else ""} in last 24 h',
                'cta': 'Visitor log',
                'href': '/visitors',
            })

        # — Per-person attendance drop (last 7d vs previous 7d)
        rows = c.execute(
            "SELECT person_id, "
            "       SUM(CASE WHEN date >= date('now','-6 days') AND check_in IS NOT NULL THEN 1 ELSE 0 END) AS recent, "
            "       SUM(CASE WHEN date >= date('now','-13 days') AND date < date('now','-6 days') "
            "                AND check_in IS NOT NULL THEN 1 ELSE 0 END) AS prev "
            "FROM attendance "
            "WHERE date >= date('now','-13 days') "
            "GROUP BY person_id"
        ).fetchall()
        for r in rows:
            if r['prev'] >= 3 and r['recent'] < r['prev'] - 1:
                person = c.execute(
                    'SELECT name FROM persons WHERE person_id = ?',
                    (r['person_id'],)).fetchone()
                nm = person['name'] if person else r['person_id']
                out.append({
                    'kind': 'warn', 'icon': 'trending_down',
                    'title': f'{nm}’s attendance dropped: '
                             f'{r["recent"]}/7 vs {r["prev"]}/7 last week',
                    'cta': 'View profile',
                    'href': f'/user/{r["person_id"]}',
                })

        # — Birthdays
        bdays = birthdays_today()
        for b in bdays:
            out.append({
                'kind': 'cake', 'icon': 'cake',
                'title': f'\U0001F382 Birthday today: {b["name"]}',
                'cta': 'Wish them',
                'href': f'/user/{b["person_id"]}',
            })

    return out[:10]


# ---------------------------------------------------------------------------
# Attendance heatmap (GitHub-style, last N days)
# ---------------------------------------------------------------------------
def attendance_heatmap(person_id: str, days: int = 90) -> dict:
    with tx() as c:
        rows = c.execute(
            "SELECT date, check_in, is_late FROM attendance "
            "WHERE person_id = ? AND date >= date('now', ?) "
            "ORDER BY date",
            (person_id, f'-{days - 1} days'),
        ).fetchall()
    by_date = {}
    for r in rows:
        if r['check_in']:
            by_date[r['date']] = 2 if r['is_late'] else 3   # late or on-time
    out = []
    from datetime import timedelta as _td
    today = date.today()
    for i in range(days):
        d = (today - _td(days=days - 1 - i)).isoformat()
        out.append({'date': d, 'level': by_date.get(d, 0)})
    return {'days': out}


# ===========================================================================
# Extended feature set (Feature Expansion build)
# ===========================================================================
import hashlib
import hmac as _hmac
import json
import secrets
from werkzeug.security import generate_password_hash as _ghp, check_password_hash as _chp

# ── Branches / sites ───────────────────────────────────────────────────────
def list_branches() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM branches ORDER BY name').fetchall()]


def get_branch(branch_id: int) -> Optional[dict]:
    with tx() as c:
        r = c.execute('SELECT * FROM branches WHERE id = ?', (branch_id,)).fetchone()
        return dict(r) if r else None


def add_branch(name: str, address: str = '', timezone: str = '',
               lat: Optional[float] = None, lng: Optional[float] = None,
               radius_m: Optional[float] = None,
               polygon_json: Optional[str] = None) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO branches(name, address, timezone, lat, lng, '
            'radius_m, polygon_json, created_at) VALUES (?,?,?,?,?,?,?,?)',
            (name.strip(), address or None, timezone or None,
             lat, lng, radius_m, polygon_json,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def update_branch(branch_id: int, **kw) -> None:
    keys = ('name', 'address', 'timezone', 'lat', 'lng', 'radius_m', 'polygon_json')
    fields = [f'{k} = ?' for k in keys if k in kw]
    args = [kw[k] for k in keys if k in kw]
    if not fields:
        return
    args.append(branch_id)
    with tx() as c:
        c.execute(f'UPDATE branches SET {", ".join(fields)} WHERE id = ?', args)


def delete_branch(branch_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM branches WHERE id = ?', (branch_id,))


# ── Shifts ─────────────────────────────────────────────────────────────────
def list_shifts() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT s.*, b.name AS branch_name, d.name AS department_name '
            'FROM shifts s '
            'LEFT JOIN branches b   ON b.id = s.branch_id '
            'LEFT JOIN departments d ON d.id = s.department_id '
            'ORDER BY s.start_time').fetchall()]


def add_shift(name: str, start: str, end: str, grace_min: int,
              branch_id: Optional[int] = None,
              department_id: Optional[int] = None,
              days_mask: str = '1111100') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO shifts(name, start_time, end_time, grace_min, '
            'branch_id, department_id, days_mask) VALUES (?,?,?,?,?,?,?)',
            (name, start, end, grace_min, branch_id, department_id, days_mask))
        return cur.lastrowid


def delete_shift(shift_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM shifts WHERE id = ?', (shift_id,))


def assign_shift(person_id: str, shift_id: int, effective: str) -> None:
    with tx() as c:
        c.execute('INSERT OR REPLACE INTO person_shift(person_id, shift_id, effective) '
                  'VALUES (?,?,?)', (person_id, shift_id, effective))


def get_person_shift(person_id: str, on_date: str) -> Optional[dict]:
    with tx() as c:
        r = c.execute(
            'SELECT s.* FROM person_shift ps '
            'JOIN shifts s ON s.id = ps.shift_id '
            'WHERE ps.person_id = ? AND ps.effective <= ? '
            'ORDER BY ps.effective DESC LIMIT 1',
            (person_id, on_date)).fetchone()
        return dict(r) if r else None


# ── Leaves ────────────────────────────────────────────────────────────────
def add_leave(person_id: str, leave_type: str, start_date: str,
              end_date: str, reason: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO leave_requests(person_id, leave_type, start_date, '
            'end_date, reason, created_at) VALUES (?,?,?,?,?,?)',
            (person_id, leave_type, start_date, end_date, reason or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def decide_leave(leave_id: int, status: str, actor: str) -> None:
    with tx() as c:
        c.execute('UPDATE leave_requests SET status = ?, decided_by = ?, '
                  'decided_at = ? WHERE id = ?',
                  (status, actor,
                   datetime.now().isoformat(timespec='seconds'), leave_id))


def list_leaves(status: Optional[str] = None,
                person_id: Optional[str] = None) -> list[dict]:
    q = ('SELECT l.*, p.name AS person_name FROM leave_requests l '
         'LEFT JOIN persons p ON p.person_id = l.person_id WHERE 1=1 ')
    args: list = []
    if status:
        q += ' AND l.status = ?'; args.append(status)
    if person_id:
        q += ' AND l.person_id = ?'; args.append(person_id)
    q += ' ORDER BY l.created_at DESC'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def is_on_leave(person_id: str, on_date: str) -> bool:
    with tx() as c:
        r = c.execute(
            "SELECT 1 FROM leave_requests WHERE person_id = ? "
            "AND status = 'approved' AND ? BETWEEN start_date AND end_date "
            "LIMIT 1", (person_id, on_date)).fetchone()
        return r is not None


# ── Timetable ─────────────────────────────────────────────────────────────
def add_timetable_slot(subject_id: int, weekday: int, start: str, end: str,
                       room: str = '', teacher: str = '',
                       active_from: Optional[str] = None,
                       active_to: Optional[str] = None) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO timetable(subject_id, weekday, start_time, end_time, '
            'room, teacher, active_from, active_to) VALUES (?,?,?,?,?,?,?,?)',
            (subject_id, weekday, start, end, room or None, teacher or None,
             active_from, active_to))
        return cur.lastrowid


def list_timetable() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT t.*, s.name AS subject_name, s.code AS subject_code '
            'FROM timetable t LEFT JOIN subjects s ON s.id = t.subject_id '
            'ORDER BY t.weekday, t.start_time').fetchall()]


def delete_timetable_slot(slot_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM timetable WHERE id = ?', (slot_id,))


def materialise_today_sessions(on_date: str) -> int:
    """Create today's class_sessions rows from the recurring timetable.
    Returns how many sessions were inserted (skips ones already present)."""
    wd = datetime.strptime(on_date, '%Y-%m-%d').weekday()
    inserted = 0
    with tx() as c:
        slots = c.execute(
            'SELECT * FROM timetable WHERE weekday = ? '
            'AND (active_from IS NULL OR active_from <= ?) '
            'AND (active_to   IS NULL OR active_to   >= ?)',
            (wd, on_date, on_date)).fetchall()
        for s in slots:
            exists = c.execute(
                'SELECT 1 FROM class_sessions '
                'WHERE subject_id = ? AND date = ? AND start_time = ?',
                (s['subject_id'], on_date, s['start_time'])).fetchone()
            if exists:
                continue
            c.execute('INSERT INTO class_sessions(subject_id, date, start_time, '
                      'end_time, notes, created_at) VALUES (?,?,?,?,?,?)',
                      (s['subject_id'], on_date, s['start_time'], s['end_time'],
                       f'auto from timetable (room {s["room"] or "-"}, {s["teacher"] or "-"})',
                       datetime.now().isoformat(timespec='seconds')))
            inserted += 1
    return inserted


# ── Holidays ──────────────────────────────────────────────────────────────
def add_holiday(date_iso: str, name: str, branch_id: Optional[int] = None) -> None:
    with tx() as c:
        c.execute('INSERT OR IGNORE INTO holidays(date, name, branch_id) '
                  'VALUES (?,?,?)', (date_iso, name, branch_id))


def list_holidays() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT h.*, b.name AS branch_name FROM holidays h '
            'LEFT JOIN branches b ON b.id = h.branch_id '
            'ORDER BY h.date').fetchall()]


def delete_holiday(holiday_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM holidays WHERE id = ?', (holiday_id,))


def is_holiday(on_date: str, branch_id: Optional[int] = None) -> bool:
    with tx() as c:
        r = c.execute(
            'SELECT 1 FROM holidays WHERE date = ? AND '
            '(branch_id IS NULL OR branch_id = ?) LIMIT 1',
            (on_date, branch_id)).fetchone()
        return r is not None


# ── Consent / erasure (DPDP / GDPR) ───────────────────────────────────────
def _consent_sig(person_id: str, purpose: str, proof_text: str) -> str:
    secret = (get_setting('consent_secret') or 'facemark-consent').encode()
    return _hmac.new(secret,
                     f'{person_id}|{purpose}|{proof_text}'.encode(),
                     hashlib.sha256).hexdigest()


def record_consent(person_id: str, purpose: str, granted: bool,
                   proof_text: str = '') -> None:
    with tx() as c:
        c.execute('INSERT INTO consents(person_id, purpose, granted, granted_at, '
                  'proof_text, proof_sig) VALUES (?,?,?,?,?,?) '
                  'ON CONFLICT(person_id, purpose) DO UPDATE SET '
                  '  granted    = excluded.granted, '
                  '  granted_at = excluded.granted_at, '
                  '  proof_text = excluded.proof_text, '
                  '  proof_sig  = excluded.proof_sig, '
                  '  revoked_at = CASE WHEN excluded.granted = 0 '
                  '                    THEN excluded.granted_at ELSE NULL END',
                  (person_id, purpose, 1 if granted else 0,
                   datetime.now().isoformat(timespec='seconds'),
                   proof_text,
                   _consent_sig(person_id, purpose, proof_text)))


def list_consents(person_id: Optional[str] = None) -> list[dict]:
    q = 'SELECT * FROM consents'
    args = []
    if person_id:
        q += ' WHERE person_id = ?'; args.append(person_id)
    q += ' ORDER BY granted_at DESC'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def consent_status(person_id: str, purpose: str) -> bool:
    with tx() as c:
        r = c.execute('SELECT granted FROM consents WHERE person_id = ? AND purpose = ?',
                      (person_id, purpose)).fetchone()
        return bool(r and r['granted'])


def request_erasure(person_id: str, actor: str) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO erasure_requests(person_id, requested_at, actor) '
            'VALUES (?,?,?)',
            (person_id, datetime.now().isoformat(timespec='seconds'), actor))
        return cur.lastrowid


def fulfil_erasure(person_id: str) -> None:
    """Hard-delete attendance, consents, sightings for the person.
    The caller is responsible for removing files on disk."""
    with tx() as c:
        c.execute('DELETE FROM attendance WHERE person_id = ?', (person_id,))
        c.execute('DELETE FROM session_attendance WHERE person_id = ?', (person_id,))
        c.execute('DELETE FROM consents WHERE person_id = ?', (person_id,))
        c.execute('DELETE FROM gps_marks WHERE person_id = ?', (person_id,))
        c.execute('DELETE FROM leave_requests WHERE person_id = ?', (person_id,))
        c.execute('DELETE FROM site_muster WHERE person_id = ?', (person_id,))
        c.execute('DELETE FROM persons WHERE person_id = ?', (person_id,))
        c.execute("UPDATE erasure_requests SET fulfilled_at = ? "
                  "WHERE person_id = ? AND fulfilled_at IS NULL",
                  (datetime.now().isoformat(timespec='seconds'), person_id))


def list_erasure_requests() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM erasure_requests ORDER BY requested_at DESC').fetchall()]


# ── Notifications log ─────────────────────────────────────────────────────
def log_notification(channel: str, recipient: str, subject: str,
                     body: str, status: str, detail: str = '') -> None:
    with tx() as c:
        c.execute('INSERT INTO notifications(channel, recipient, subject, body, '
                  'status, detail, created_at) VALUES (?,?,?,?,?,?,?)',
                  (channel, recipient, subject, body, status, detail,
                   datetime.now().isoformat(timespec='seconds')))


def list_notifications(limit: int = 200) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?',
            (limit,)).fetchall()]


# ── API keys + webhooks ───────────────────────────────────────────────────
def create_api_key(label: str, scopes: str = 'read') -> tuple[str, int]:
    raw = 'fm_' + secrets.token_urlsafe(28)
    h = hashlib.sha256(raw.encode()).hexdigest()
    with tx() as c:
        cur = c.execute(
            'INSERT INTO api_keys(label, key_hash, scopes, created_at) '
            'VALUES (?,?,?,?)',
            (label, h, scopes, datetime.now().isoformat(timespec='seconds')))
        return raw, cur.lastrowid


def list_api_keys() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT id, label, scopes, created_at, last_used, revoked '
            'FROM api_keys ORDER BY created_at DESC').fetchall()]


def revoke_api_key(key_id: int) -> None:
    with tx() as c:
        c.execute('UPDATE api_keys SET revoked = 1 WHERE id = ?', (key_id,))


def verify_api_key(raw_key: str) -> Optional[dict]:
    if not raw_key:
        return None
    h = hashlib.sha256(raw_key.encode()).hexdigest()
    with tx() as c:
        r = c.execute('SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0',
                      (h,)).fetchone()
        if r:
            c.execute('UPDATE api_keys SET last_used = ? WHERE id = ?',
                      (datetime.now().isoformat(timespec='seconds'), r['id']))
            return dict(r)
    return None


def add_webhook(url: str, events: str, secret: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO webhooks(url, events, secret, created_at) '
            'VALUES (?,?,?,?)',
            (url, events, secret or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_webhooks() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM webhooks ORDER BY created_at DESC').fetchall()]


def delete_webhook(webhook_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM webhooks WHERE id = ?', (webhook_id,))


def update_webhook_status(webhook_id: int, status: str) -> None:
    with tx() as c:
        c.execute('UPDATE webhooks SET last_status = ? WHERE id = ?',
                  (status, webhook_id))


# ── GPS marks ─────────────────────────────────────────────────────────────
def log_gps_mark(person_id: str, branch_id: Optional[int], lat: float, lng: float,
                 accuracy_m: Optional[float], status: str, reason: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO gps_marks(person_id, branch_id, lat, lng, '
            'accuracy_m, status, reason, created_at) VALUES (?,?,?,?,?,?,?,?)',
            (person_id, branch_id, lat, lng, accuracy_m, status, reason or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def last_gps_mark(person_id: str) -> Optional[dict]:
    with tx() as c:
        r = c.execute(
            'SELECT * FROM gps_marks WHERE person_id = ? '
            'ORDER BY created_at DESC LIMIT 1', (person_id,)).fetchone()
        return dict(r) if r else None


# ── Contractors / site muster ─────────────────────────────────────────────
def add_contractor(name: str, contact: str = '',
                   site_id: Optional[int] = None) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT OR IGNORE INTO contractors(name, contact, site_id, created_at) '
            'VALUES (?,?,?,?)',
            (name, contact or None, site_id,
             datetime.now().isoformat(timespec='seconds')))
        if cur.lastrowid:
            return cur.lastrowid
        r = c.execute('SELECT id FROM contractors WHERE name = ?', (name,)).fetchone()
        return r['id'] if r else 0


def list_contractors() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT c.*, b.name AS site_name FROM contractors c '
            'LEFT JOIN branches b ON b.id = c.site_id ORDER BY c.name').fetchall()]


def site_muster_for(date_iso: str,
                    branch_id: Optional[int] = None,
                    contractor_id: Optional[int] = None) -> list[dict]:
    q = ('SELECT sm.*, p.name AS person_name, c.name AS contractor_name, '
         'b.name AS branch_name '
         'FROM site_muster sm '
         'LEFT JOIN persons p ON p.person_id = sm.person_id '
         'LEFT JOIN contractors c ON c.id = sm.contractor_id '
         'LEFT JOIN branches b ON b.id = sm.branch_id '
         'WHERE sm.date = ? ')
    args: list = [date_iso]
    if branch_id:
        q += ' AND sm.branch_id = ?'; args.append(branch_id)
    if contractor_id:
        q += ' AND sm.contractor_id = ?'; args.append(contractor_id)
    q += ' ORDER BY p.name'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def upsert_muster(person_id: str, on_date: str, hours: float, daily_rate: float,
                  overtime_hr: float = 0.0,
                  branch_id: Optional[int] = None,
                  contractor_id: Optional[int] = None,
                  note: str = '') -> None:
    with tx() as c:
        c.execute(
            'INSERT INTO site_muster(person_id, date, hours, daily_rate, '
            'overtime_hr, branch_id, contractor_id, note) VALUES (?,?,?,?,?,?,?,?) '
            'ON CONFLICT(person_id, date) DO UPDATE SET '
            '  hours = excluded.hours, daily_rate = excluded.daily_rate, '
            '  overtime_hr = excluded.overtime_hr, branch_id = excluded.branch_id, '
            '  contractor_id = excluded.contractor_id, note = excluded.note',
            (person_id, on_date, hours, daily_rate, overtime_hr,
             branch_id, contractor_id, note or None))


def log_ppe_incident(person_id: Optional[str], branch_id: Optional[int],
                     detected: str, missing: str, snapshot: str = '') -> None:
    with tx() as c:
        c.execute('INSERT INTO ppe_incidents(person_id, branch_id, detected, '
                  'missing, snapshot, seen_at) VALUES (?,?,?,?,?,?)',
                  (person_id, branch_id, detected, missing, snapshot or None,
                   datetime.now().isoformat(timespec='seconds')))


def list_ppe_incidents(limit: int = 100) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT i.*, p.name AS person_name, b.name AS branch_name '
            'FROM ppe_incidents i '
            'LEFT JOIN persons p ON p.person_id = i.person_id '
            'LEFT JOIN branches b ON b.id = i.branch_id '
            'ORDER BY i.seen_at DESC LIMIT ?', (limit,)).fetchall()]


# ── RBAC helpers ─────────────────────────────────────────────────────────
def create_admin(username: str, password: str, role: str = 'staff',
                 email: Optional[str] = None,
                 branch_id: Optional[int] = None) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO admin_users(username, password_hash, role, email, '
            'branch_id, created_at) VALUES (?,?,?,?,?,?)',
            (username, _ghp(password), role, email, branch_id,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_admins() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT id, username, role, email, branch_id, created_at '
            'FROM admin_users ORDER BY username').fetchall()]


def delete_admin(admin_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM admin_users WHERE id = ?', (admin_id,))


def get_admin_role(username: str) -> str:
    with tx() as c:
        r = c.execute('SELECT role FROM admin_users WHERE username = ?',
                      (username,)).fetchone()
        return r['role'] if r else 'admin'


# ── PIN / QR per-person ──────────────────────────────────────────────────
def set_person_pin(person_id: str, pin: str) -> None:
    with tx() as c:
        c.execute('UPDATE persons SET pin_hash = ? WHERE person_id = ?',
                  (_ghp(pin), person_id))


def verify_person_pin(person_id: str, pin: str) -> bool:
    with tx() as c:
        r = c.execute('SELECT pin_hash FROM persons WHERE person_id = ?',
                      (person_id,)).fetchone()
        if not r or not r['pin_hash']:
            return False
        return _chp(r['pin_hash'], pin)


def rotate_qr_secret(person_id: str) -> str:
    sec = secrets.token_urlsafe(20)
    with tx() as c:
        c.execute('UPDATE persons SET qr_secret = ? WHERE person_id = ?',
                  (sec, person_id))
    return sec


def verify_qr_secret(person_id: str, sec: str) -> bool:
    with tx() as c:
        r = c.execute('SELECT qr_secret FROM persons WHERE person_id = ?',
                      (person_id,)).fetchone()
        return bool(r and r['qr_secret'] and _hmac.compare_digest(r['qr_secret'], sec))


# ── Data-retention auto-purge ────────────────────────────────────────────
def purge_old_data(visitor_days: int = 30,
                   notification_days: int = 90,
                   audit_days: int = 365,
                   gps_days: int = 365) -> dict:
    n = {'visitors': 0, 'notifications': 0, 'audit': 0, 'gps': 0}
    with tx() as c:
        n['visitors'] = c.execute(
            "DELETE FROM visitors WHERE seen_at < datetime('now', ?)",
            (f'-{visitor_days} days',)).rowcount
        n['notifications'] = c.execute(
            "DELETE FROM notifications WHERE created_at < datetime('now', ?)",
            (f'-{notification_days} days',)).rowcount
        n['audit'] = c.execute(
            "DELETE FROM audit_log WHERE created_at < datetime('now', ?)",
            (f'-{audit_days} days',)).rowcount
        n['gps'] = c.execute(
            "DELETE FROM gps_marks WHERE created_at < datetime('now', ?)",
            (f'-{gps_days} days',)).rowcount
    return n


# ── Predictive at-risk: per-person trend score ───────────────────────────
def at_risk_persons(min_drop: float = 0.25, window: int = 14) -> list[dict]:
    """Persons whose recent attendance ratio dropped by `min_drop` vs prior window."""
    with tx() as c:
        rows = c.execute(
            "SELECT p.person_id, p.name, "
            "  AVG(CASE WHEN a.date >= date('now', ?) "
            "           AND a.check_in IS NOT NULL THEN 1.0 ELSE 0.0 END) AS recent, "
            "  AVG(CASE WHEN a.date <  date('now', ?) "
            "           AND a.date >= date('now', ?) "
            "           AND a.check_in IS NOT NULL THEN 1.0 ELSE 0.0 END) AS prior "
            "FROM persons p LEFT JOIN attendance a ON a.person_id = p.person_id "
            "WHERE a.date >= date('now', ?) OR a.date IS NULL "
            "GROUP BY p.person_id",
            (f'-{window} days', f'-{window} days',
             f'-{window * 2} days', f'-{window * 2} days')).fetchall()
    out = []
    for r in rows:
        recent = float(r['recent'] or 0)
        prior = float(r['prior'] or 0)
        if prior >= 0.4 and recent + min_drop <= prior:
            out.append({
                'person_id': r['person_id'], 'name': r['name'],
                'recent_pct': round(recent * 100, 1),
                'prior_pct': round(prior * 100, 1),
                'drop_pct': round((prior - recent) * 100, 1),
            })
    out.sort(key=lambda x: -x['drop_pct'])
    return out


# ===========================================================================
# RFID / NFC fallback
# ===========================================================================
def set_person_rfid(person_id: str, uid: str) -> None:
    with tx() as c:
        c.execute('UPDATE persons SET rfid_uid = ? WHERE person_id = ?',
                  (uid.strip(), person_id))


def find_person_by_rfid(uid: str) -> Optional[dict]:
    with tx() as c:
        r = c.execute(
            'SELECT p.*, d.name AS department_name FROM persons p '
            'LEFT JOIN departments d ON d.id = p.department_id '
            'WHERE p.rfid_uid = ?', (uid.strip(),)).fetchone()
        return dict(r) if r else None


# ===========================================================================
# Multi-camera registry
# ===========================================================================
def add_camera(name: str, url: str, branch_id: Optional[int] = None,
               purpose: str = 'attendance', enabled: bool = True) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO cameras(name, url, branch_id, purpose, enabled, created_at) '
            'VALUES (?,?,?,?,?,?)',
            (name, url, branch_id, purpose, 1 if enabled else 0,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_cameras(purpose: Optional[str] = None,
                 enabled_only: bool = False) -> list[dict]:
    q = ('SELECT c.*, b.name AS branch_name FROM cameras c '
         'LEFT JOIN branches b ON b.id = c.branch_id WHERE 1=1 ')
    args: list = []
    if purpose:
        q += ' AND c.purpose = ?'; args.append(purpose)
    if enabled_only:
        q += ' AND c.enabled = 1'
    q += ' ORDER BY c.name'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def delete_camera(camera_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM cameras WHERE id = ?', (camera_id,))


def toggle_camera(camera_id: int, enabled: bool) -> None:
    with tx() as c:
        c.execute('UPDATE cameras SET enabled = ? WHERE id = ?',
                  (1 if enabled else 0, camera_id))


def get_camera(camera_id: int) -> Optional[dict]:
    with tx() as c:
        r = c.execute('SELECT * FROM cameras WHERE id = ?',
                      (camera_id,)).fetchone()
        return dict(r) if r else None


# ===========================================================================
# Bus / transport
# ===========================================================================
def add_bus_route(name: str, driver: str = '', vehicle_no: str = '',
                  branch_id: Optional[int] = None) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO bus_routes(name, driver, vehicle_no, branch_id, created_at) '
            'VALUES (?,?,?,?,?)',
            (name, driver or None, vehicle_no or None, branch_id,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_bus_routes() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT r.*, b.name AS branch_name FROM bus_routes r '
            'LEFT JOIN branches b ON b.id = r.branch_id ORDER BY r.name').fetchall()]


def delete_bus_route(route_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM bus_routes WHERE id = ?', (route_id,))


def add_bus_stop(route_id: int, name: str, seq: int,
                 lat: Optional[float] = None,
                 lng: Optional[float] = None) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO bus_stops(route_id, name, seq, lat, lng) '
            'VALUES (?,?,?,?,?)', (route_id, name, seq, lat, lng))
        return cur.lastrowid


def list_bus_stops(route_id: int) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM bus_stops WHERE route_id = ? ORDER BY seq',
            (route_id,)).fetchall()]


def assign_bus(person_id: str, route_id: int,
               stop_id: Optional[int] = None) -> None:
    with tx() as c:
        c.execute('INSERT OR REPLACE INTO bus_assignments(person_id, route_id, stop_id) '
                  'VALUES (?,?,?)', (person_id, route_id, stop_id))


def log_boarding(person_id: str, route_id: Optional[int], stop_id: Optional[int],
                 direction: str, location: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO bus_boardings(person_id, route_id, stop_id, direction, '
            'seen_at, location) VALUES (?,?,?,?,?,?)',
            (person_id, route_id, stop_id, direction,
             datetime.now().isoformat(timespec='seconds'),
             location or None))
        return cur.lastrowid


def list_boardings(limit: int = 100) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT bb.*, p.name AS person_name, r.name AS route_name, '
            's.name AS stop_name '
            'FROM bus_boardings bb '
            'LEFT JOIN persons p ON p.person_id = bb.person_id '
            'LEFT JOIN bus_routes r ON r.id = bb.route_id '
            'LEFT JOIN bus_stops s ON s.id = bb.stop_id '
            'ORDER BY bb.seen_at DESC LIMIT ?', (limit,)).fetchall()]


# ===========================================================================
# Substitutions
# ===========================================================================
def add_substitution(session_id: int, substitute: str,
                     original: str = '', note: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO substitutions(session_id, original, substitute, note, created_at) '
            'VALUES (?,?,?,?,?)',
            (session_id, original or None, substitute, note or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_substitutions(session_id: Optional[int] = None) -> list[dict]:
    q = ('SELECT s.*, cs.date, cs.start_time, sub.name AS subject_name '
         'FROM substitutions s '
         'LEFT JOIN class_sessions cs ON cs.id = s.session_id '
         'LEFT JOIN subjects sub ON sub.id = cs.subject_id ')
    args: list = []
    if session_id:
        q += ' WHERE s.session_id = ?'; args.append(session_id)
    q += ' ORDER BY s.created_at DESC'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ===========================================================================
# Sensor readings
# ===========================================================================
def log_sensor(kind: str, person_id: Optional[str] = None,
               branch_id: Optional[int] = None,
               value_num: Optional[float] = None,
               value_text: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO sensor_readings(person_id, branch_id, kind, '
            'value_num, value_text, seen_at) VALUES (?,?,?,?,?,?)',
            (person_id, branch_id, kind, value_num, value_text or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_sensor_readings(kind: Optional[str] = None, limit: int = 200) -> list[dict]:
    q = ('SELECT sr.*, p.name AS person_name, b.name AS branch_name '
         'FROM sensor_readings sr '
         'LEFT JOIN persons p ON p.person_id = sr.person_id '
         'LEFT JOIN branches b ON b.id = sr.branch_id WHERE 1=1 ')
    args: list = []
    if kind:
        q += ' AND sr.kind = ?'; args.append(kind)
    q += ' ORDER BY sr.seen_at DESC LIMIT ?'
    args.append(limit)
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ===========================================================================
# Door relay log
# ===========================================================================
def log_door(branch_id: Optional[int], person_id: Optional[str],
             relay_url: str, http_status: str) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO door_log(branch_id, person_id, relay_url, http_status, seen_at) '
            'VALUES (?,?,?,?,?)',
            (branch_id, person_id, relay_url, http_status,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_door_log(limit: int = 200) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT dl.*, p.name AS person_name, b.name AS branch_name '
            'FROM door_log dl '
            'LEFT JOIN persons p ON p.person_id = dl.person_id '
            'LEFT JOIN branches b ON b.id = dl.branch_id '
            'ORDER BY dl.seen_at DESC LIMIT ?', (limit,)).fetchall()]


# ===========================================================================
# Exam continuous-presence
# ===========================================================================
def create_exam(name: str, start_at: str, end_at: str,
                branch_id: Optional[int] = None,
                department_id: Optional[int] = None,
                check_every_sec: int = 60) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO exam_sessions(name, branch_id, department_id, '
            'start_at, end_at, check_every_sec, created_at) '
            'VALUES (?,?,?,?,?,?,?)',
            (name, branch_id, department_id, start_at, end_at,
             check_every_sec, datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_exams() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT e.*, b.name AS branch_name, d.name AS department_name '
            'FROM exam_sessions e '
            'LEFT JOIN branches b ON b.id = e.branch_id '
            'LEFT JOIN departments d ON d.id = e.department_id '
            'ORDER BY e.start_at DESC').fetchall()]


def get_active_exam() -> Optional[dict]:
    with tx() as c:
        r = c.execute(
            "SELECT * FROM exam_sessions "
            "WHERE active = 1 AND datetime(start_at) <= datetime('now') "
            "AND datetime(end_at) >= datetime('now') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(r) if r else None


def end_exam(exam_id: int) -> None:
    with tx() as c:
        c.execute('UPDATE exam_sessions SET active = 0 WHERE id = ?', (exam_id,))


def log_exam_alert(exam_id: int, person_id: str, kind: str,
                   detail: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO exam_alerts(exam_id, person_id, kind, detail, seen_at) '
            'VALUES (?,?,?,?,?)',
            (exam_id, person_id, kind, detail or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_exam_alerts(exam_id: int) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT ea.*, p.name AS person_name FROM exam_alerts ea '
            'LEFT JOIN persons p ON p.person_id = ea.person_id '
            'WHERE ea.exam_id = ? ORDER BY ea.seen_at DESC',
            (exam_id,)).fetchall()]


# ===========================================================================
# Backup log
# ===========================================================================
def log_backup(destination: str, bytes_: int, status: str, detail: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO backup_log(destination, bytes, status, detail, created_at) '
            'VALUES (?,?,?,?,?)',
            (destination, bytes_, status, detail or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_backups(limit: int = 50) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM backup_log ORDER BY created_at DESC LIMIT ?',
            (limit,)).fetchall()]


# ===========================================================================
# Full per-person export (DPDP / GDPR Art 20 portability)
# ===========================================================================
def person_full_export(person_id: str) -> dict:
    """Return every row referencing this person."""
    with tx() as c:
        def all_(q, *a):
            return [dict(r) for r in c.execute(q, a).fetchall()]
        p = c.execute(
            'SELECT p.*, d.name AS department_name '
            'FROM persons p LEFT JOIN departments d ON d.id = p.department_id '
            'WHERE p.person_id = ?', (person_id,)).fetchone()
        return {
            'person':         dict(p) if p else None,
            'attendance':     all_('SELECT * FROM attendance WHERE person_id = ? ORDER BY date',
                                   person_id),
            'session_attendance': all_('SELECT * FROM session_attendance WHERE person_id = ?', person_id),
            'leaves':         all_('SELECT * FROM leave_requests WHERE person_id = ?', person_id),
            'consents':       all_('SELECT * FROM consents WHERE person_id = ?', person_id),
            'gps_marks':      all_('SELECT * FROM gps_marks WHERE person_id = ?', person_id),
            'sensor_readings': all_('SELECT * FROM sensor_readings WHERE person_id = ?', person_id),
            'site_muster':    all_('SELECT * FROM site_muster WHERE person_id = ?', person_id),
            'bus_boardings':  all_('SELECT * FROM bus_boardings WHERE person_id = ?', person_id),
            'ppe_incidents':  all_('SELECT * FROM ppe_incidents WHERE person_id = ?', person_id),
            'notifications':  all_('SELECT * FROM notifications WHERE recipient IN '
                                   '(SELECT email FROM persons WHERE person_id = ? '
                                   'UNION SELECT guardian_email FROM persons WHERE person_id = ?)',
                                   person_id, person_id),
        }


# ===========================================================================
# Enterprise Edition (N1-N20) helpers
# ===========================================================================

# ── N17 Tenants ────────────────────────────────────────────────────────────
def add_tenant(slug: str, name: str, plan: str = 'starter',
               parent_id: Optional[int] = None) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO tenants(slug, name, plan, parent_id, created_at) '
            'VALUES (?,?,?,?,?)',
            (slug.lower(), name, plan, parent_id,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_tenants(parent_id: Optional[int] = None) -> list[dict]:
    q = 'SELECT t.*, (SELECT COUNT(*) FROM persons WHERE tenant_id = t.id) AS people FROM tenants t WHERE 1=1 '
    args: list = []
    if parent_id is not None:
        q += ' AND parent_id = ?'; args.append(parent_id)
    q += ' ORDER BY t.name'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def get_tenant(tenant_id: int) -> Optional[dict]:
    with tx() as c:
        r = c.execute('SELECT * FROM tenants WHERE id = ?', (tenant_id,)).fetchone()
        return dict(r) if r else None


def get_tenant_by_slug(slug: str) -> Optional[dict]:
    with tx() as c:
        r = c.execute('SELECT * FROM tenants WHERE slug = ?', (slug.lower(),)).fetchone()
        return dict(r) if r else None


def update_tenant(tenant_id: int, **kw) -> None:
    keys = ('name', 'plan', 'brand_color', 'brand_logo', 'seats', 'enabled')
    fields = [f'{k} = ?' for k in keys if k in kw]
    args = [kw[k] for k in keys if k in kw]
    if not fields:
        return
    args.append(tenant_id)
    with tx() as c:
        c.execute(f'UPDATE tenants SET {", ".join(fields)} WHERE id = ?', args)


def delete_tenant(tenant_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM tenants WHERE id = ?', (tenant_id,))


# ── N1 SSO providers ──────────────────────────────────────────────────────
def add_sso_provider(kind: str, name: str, **kw) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO sso_providers(kind, name, tenant_id, issuer, client_id, '
            'client_secret, auth_url, token_url, userinfo_url, metadata_url, '
            'domain, default_role, created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (kind, name,
             kw.get('tenant_id'), kw.get('issuer'), kw.get('client_id'),
             kw.get('client_secret'), kw.get('auth_url'), kw.get('token_url'),
             kw.get('userinfo_url'), kw.get('metadata_url'),
             kw.get('domain'), kw.get('default_role', 'staff'),
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_sso_providers(tenant_id: Optional[int] = None,
                       enabled_only: bool = False) -> list[dict]:
    q = 'SELECT * FROM sso_providers WHERE 1=1 '
    args: list = []
    if tenant_id is not None:
        q += ' AND (tenant_id = ? OR tenant_id IS NULL)'; args.append(tenant_id)
    if enabled_only:
        q += ' AND enabled = 1'
    q += ' ORDER BY name'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def get_sso_provider(provider_id: int) -> Optional[dict]:
    with tx() as c:
        r = c.execute('SELECT * FROM sso_providers WHERE id = ?',
                      (provider_id,)).fetchone()
        return dict(r) if r else None


def delete_sso_provider(provider_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM sso_providers WHERE id = ?', (provider_id,))


def upsert_admin_from_sso(provider_id: int, subject: str, email: str,
                          name: str, role: str = 'staff') -> str:
    """Create or update an admin row from an SSO assertion. Returns username."""
    with tx() as c:
        r = c.execute(
            'SELECT id, username FROM admin_users WHERE sso_subject = ? AND sso_provider = ?',
            (subject, provider_id)).fetchone()
        if r:
            c.execute('UPDATE admin_users SET email = ? WHERE id = ?',
                      (email, r['id']))
            return r['username']
        # Fall back to email match
        if email:
            r2 = c.execute('SELECT id, username FROM admin_users WHERE email = ?',
                           (email,)).fetchone()
            if r2:
                c.execute('UPDATE admin_users SET sso_subject = ?, sso_provider = ? WHERE id = ?',
                          (subject, provider_id, r2['id']))
                return r2['username']
        username = email or subject
        c.execute(
            'INSERT INTO admin_users(username, password_hash, role, email, '
            'sso_subject, sso_provider, created_at) VALUES (?,?,?,?,?,?,?)',
            (username,
             generate_password_hash(secrets.token_hex(16)),  # unusable local pw
             role, email, subject, provider_id,
             datetime.now().isoformat(timespec='seconds')))
        return username


# ── N2 SCIM ────────────────────────────────────────────────────────────────
def add_scim_client(name: str, tenant_id: Optional[int] = None) -> tuple[str, int]:
    raw = 'scim_' + secrets.token_urlsafe(24)
    h = hashlib.sha256(raw.encode()).hexdigest()
    with tx() as c:
        cur = c.execute(
            'INSERT INTO scim_clients(name, tenant_id, bearer_hash, created_at) '
            'VALUES (?,?,?,?)',
            (name, tenant_id, h,
             datetime.now().isoformat(timespec='seconds')))
        return raw, cur.lastrowid


def verify_scim_token(raw: str) -> Optional[dict]:
    if not raw:
        return None
    h = hashlib.sha256(raw.encode()).hexdigest()
    with tx() as c:
        r = c.execute(
            'SELECT * FROM scim_clients WHERE bearer_hash = ? AND enabled = 1',
            (h,)).fetchone()
        if r:
            c.execute('UPDATE scim_clients SET last_seen = ? WHERE id = ?',
                      (datetime.now().isoformat(timespec='seconds'), r['id']))
            return dict(r)
    return None


def list_scim_clients() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT id, name, tenant_id, enabled, last_seen, created_at '
            'FROM scim_clients ORDER BY created_at DESC').fetchall()]


def delete_scim_client(client_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM scim_clients WHERE id = ?', (client_id,))


# ── N3 SIEM sinks + extended audit ────────────────────────────────────────
def add_siem_sink(name: str, url: str, auth_header: str = '',
                  fmt: str = 'json') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO siem_sinks(name, url, auth_header, fmt, created_at) '
            'VALUES (?,?,?,?,?)',
            (name, url, auth_header or None, fmt,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_siem_sinks(enabled_only: bool = False) -> list[dict]:
    q = 'SELECT * FROM siem_sinks WHERE 1=1 '
    if enabled_only:
        q += ' AND enabled = 1'
    q += ' ORDER BY name'
    with tx() as c:
        return [dict(r) for r in c.execute(q).fetchall()]


def delete_siem_sink(sink_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM siem_sinks WHERE id = ?', (sink_id,))


def update_siem_sink_status(sink_id: int, status: str) -> None:
    with tx() as c:
        c.execute('UPDATE siem_sinks SET last_status = ?, last_sent = ? WHERE id = ?',
                  (status, datetime.now().isoformat(timespec='seconds'), sink_id))


def audit_ext(category: str, action: str, actor: Optional[str] = None,
              target: Optional[str] = None, detail: str = '',
              ip: Optional[str] = None, user_agent: Optional[str] = None) -> int:
    """Higher-fidelity audit row. Categorised so retention can differ."""
    with tx() as c:
        cur = c.execute(
            'INSERT INTO audit_extended(category, actor, target, action, detail, '
            'ip, user_agent, created_at) VALUES (?,?,?,?,?,?,?,?)',
            (category, actor, target, action, detail or None,
             ip, user_agent,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def pending_audit_for_siem(limit: int = 200) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM audit_extended WHERE streamed = 0 ORDER BY id LIMIT ?',
            (limit,)).fetchall()]


def mark_audit_streamed(ids: list[int]) -> None:
    if not ids:
        return
    qmarks = ','.join(['?'] * len(ids))
    with tx() as c:
        c.execute(f'UPDATE audit_extended SET streamed = 1 WHERE id IN ({qmarks})', ids)


def purge_audit_by_category(category: str, older_than_days: int) -> int:
    """Tiered retention — different categories get different lifetimes."""
    with tx() as c:
        cur = c.execute(
            "DELETE FROM audit_extended WHERE category = ? "
            "AND created_at < datetime('now', ?)",
            (category, f'-{older_than_days} days'))
        return cur.rowcount


# ── N5 Spoof events ────────────────────────────────────────────────────────
def log_spoof_event(kind: str, person_id: Optional[str] = None,
                    score: Optional[float] = None,
                    snapshot: str = '', detail: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO spoof_events(person_id, kind, score, snapshot, detail, seen_at) '
            'VALUES (?,?,?,?,?,?)',
            (person_id, kind, score, snapshot or None, detail or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_spoof_events(limit: int = 200) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT s.*, p.name AS person_name FROM spoof_events s '
            'LEFT JOIN persons p ON p.person_id = s.person_id '
            'ORDER BY s.seen_at DESC LIMIT ?', (limit,)).fetchall()]


# ── N6 Tailgating / access events ─────────────────────────────────────────
def log_access_event(kind: str, branch_id: Optional[int] = None,
                     person_id: Optional[str] = None,
                     face_count: Optional[int] = None,
                     direction: str = '',
                     snapshot: str = '', detail: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO access_events(branch_id, person_id, kind, face_count, '
            'direction, snapshot, detail, seen_at) VALUES (?,?,?,?,?,?,?,?)',
            (branch_id, person_id, kind, face_count, direction or None,
             snapshot or None, detail or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_access_events(limit: int = 200) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT ae.*, p.name AS person_name, b.name AS branch_name '
            'FROM access_events ae '
            'LEFT JOIN persons p ON p.person_id = ae.person_id '
            'LEFT JOIN branches b ON b.id = ae.branch_id '
            'ORDER BY ae.seen_at DESC LIMIT ?', (limit,)).fetchall()]


def last_attendance_dir(person_id: str) -> str:
    """For anti-passback: was the last event 'in' or 'out'?"""
    with tx() as c:
        r = c.execute(
            'SELECT check_in, check_out FROM attendance WHERE person_id = ? '
            "AND date = date('now') LIMIT 1", (person_id,)).fetchone()
    if not r:
        return ''
    if r['check_out']: return 'out'
    if r['check_in']:  return 'in'
    return ''


# ── N7 KMS keys ───────────────────────────────────────────────────────────
def add_kms_key(label: str, kms_kind: str, key_ref: str,
                wrapped_dek: bytes = b'',
                tenant_id: Optional[int] = None) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO kms_keys(tenant_id, label, kms_kind, key_ref, '
            'wrapped_dek, created_at) VALUES (?,?,?,?,?,?)',
            (tenant_id, label, kms_kind, key_ref, wrapped_dek,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_kms_keys(tenant_id: Optional[int] = None) -> list[dict]:
    q = 'SELECT id, tenant_id, label, kms_kind, key_ref, rotated_at, enabled, created_at FROM kms_keys WHERE 1=1 '
    args: list = []
    if tenant_id is not None:
        q += ' AND (tenant_id = ? OR tenant_id IS NULL)'; args.append(tenant_id)
    q += ' ORDER BY created_at DESC'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def delete_kms_key(key_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM kms_keys WHERE id = ?', (key_id,))


# ── N9 Chat check-ins ─────────────────────────────────────────────────────
def log_chat_checkin(channel: str, chat_user_id: str, event: str,
                     workspace: str = '', person_id: Optional[str] = None,
                     selfie: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO chat_checkins(channel, workspace, chat_user_id, '
            'person_id, event, selfie, seen_at) VALUES (?,?,?,?,?,?,?)',
            (channel, workspace or None, chat_user_id, person_id, event,
             selfie or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def find_person_by_chat_user(chat_user_id: str) -> Optional[dict]:
    with tx() as c:
        r = c.execute('SELECT * FROM persons WHERE chat_user_id = ?',
                      (chat_user_id,)).fetchone()
        return dict(r) if r else None


# ── N10 Presence signals ──────────────────────────────────────────────────
def log_presence(person_id: str, source: str, detail: str = '',
                 score: float = 1.0) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO presence_signals(person_id, source, detail, score, seen_at) '
            'VALUES (?,?,?,?,?)',
            (person_id, source, detail or None, score,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_presence(person_id: Optional[str] = None,
                  limit: int = 200) -> list[dict]:
    q = 'SELECT * FROM presence_signals WHERE 1=1 '
    args: list = []
    if person_id:
        q += ' AND person_id = ?'; args.append(person_id)
    q += ' ORDER BY seen_at DESC LIMIT ?'
    args.append(limit)
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ── N12 Workflows + runs ──────────────────────────────────────────────────
def add_workflow(name: str, trigger: str, steps_json: str) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO workflows(name, trigger, steps_json, created_at) '
            'VALUES (?,?,?,?)',
            (name, trigger, steps_json,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_workflows(trigger: Optional[str] = None) -> list[dict]:
    q = 'SELECT * FROM workflows WHERE 1=1 '
    args: list = []
    if trigger:
        q += ' AND trigger = ?'; args.append(trigger)
    q += ' ORDER BY name'
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def get_workflow(workflow_id: int) -> Optional[dict]:
    with tx() as c:
        r = c.execute('SELECT * FROM workflows WHERE id = ?',
                      (workflow_id,)).fetchone()
        return dict(r) if r else None


def delete_workflow(workflow_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM workflows WHERE id = ?', (workflow_id,))


def start_workflow_run(workflow_id: int, subject: str, requester: str) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO workflow_runs(workflow_id, subject, requester, created_at) '
            'VALUES (?,?,?,?)',
            (workflow_id, subject, requester,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def decide_workflow_step(run_id: int, actor: str, decision: str,
                         note: str = '') -> dict:
    import json as _json
    with tx() as c:
        r = c.execute('SELECT * FROM workflow_runs WHERE id = ?', (run_id,)).fetchone()
        if not r:
            return {'ok': False, 'error': 'no-run'}
        if r['state'] != 'pending':
            return {'ok': False, 'error': 'closed'}
        wf = c.execute('SELECT * FROM workflows WHERE id = ?', (r['workflow_id'],)).fetchone()
        if not wf:
            return {'ok': False, 'error': 'no-workflow'}
        steps = _json.loads(wf['steps_json'])
        decisions = _json.loads(r['decisions_json'])
        decisions.append({'step': r['step_idx'], 'actor': actor,
                          'decision': decision, 'note': note,
                          'at': datetime.now().isoformat(timespec='seconds')})
        if decision == 'rejected':
            c.execute(
                'UPDATE workflow_runs SET state = "rejected", decisions_json = ?, closed_at = ? '
                'WHERE id = ?',
                (_json.dumps(decisions),
                 datetime.now().isoformat(timespec='seconds'), run_id))
            return {'ok': True, 'state': 'rejected'}
        nxt = r['step_idx'] + 1
        if nxt >= len(steps):
            c.execute(
                'UPDATE workflow_runs SET state = "approved", step_idx = ?, '
                'decisions_json = ?, closed_at = ? WHERE id = ?',
                (nxt, _json.dumps(decisions),
                 datetime.now().isoformat(timespec='seconds'), run_id))
            return {'ok': True, 'state': 'approved'}
        c.execute(
            'UPDATE workflow_runs SET step_idx = ?, decisions_json = ? WHERE id = ?',
            (nxt, _json.dumps(decisions), run_id))
        return {'ok': True, 'state': 'pending', 'next_step': nxt}


def list_workflow_runs(state: Optional[str] = None,
                       limit: int = 100) -> list[dict]:
    q = ('SELECT wr.*, w.name AS workflow_name, w.trigger '
         'FROM workflow_runs wr LEFT JOIN workflows w ON w.id = wr.workflow_id '
         'WHERE 1=1 ')
    args: list = []
    if state:
        q += ' AND wr.state = ?'; args.append(state)
    q += ' ORDER BY wr.created_at DESC LIMIT ?'
    args.append(limit)
    with tx() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ── N13 Copilot log ───────────────────────────────────────────────────────
def log_copilot(actor: str, question: str, intent: str = '',
                sql_run: str = '', rows_out: int = 0,
                success: bool = True) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO copilot_log(actor, question, intent, sql_run, rows_out, '
            'success, answered_at) VALUES (?,?,?,?,?,?,?)',
            (actor, question, intent or None, sql_run or None, rows_out,
             1 if success else 0,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def recent_copilot(limit: int = 50) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM copilot_log ORDER BY answered_at DESC LIMIT ?',
            (limit,)).fetchall()]


# ── N15 Connectors ────────────────────────────────────────────────────────
def add_connector(kind: str, name: str, endpoint: str = '',
                  api_key: str = '', api_secret: str = '',
                  config_json: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO connectors(kind, name, endpoint, api_key, api_secret, '
            'config_json, created_at) VALUES (?,?,?,?,?,?,?)',
            (kind, name, endpoint or None, api_key or None, api_secret or None,
             config_json or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def list_connectors(enabled_only: bool = False) -> list[dict]:
    q = 'SELECT * FROM connectors WHERE 1=1 '
    if enabled_only:
        q += ' AND enabled = 1'
    q += ' ORDER BY name'
    with tx() as c:
        return [dict(r) for r in c.execute(q).fetchall()]


def delete_connector(connector_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM connectors WHERE id = ?', (connector_id,))


def update_connector_status(connector_id: int, status: str) -> None:
    with tx() as c:
        c.execute('UPDATE connectors SET last_status = ?, last_sync = ? WHERE id = ?',
                  (status, datetime.now().isoformat(timespec='seconds'),
                   connector_id))


# ── N16 Rooms + occupancy ─────────────────────────────────────────────────
def add_room(name: str, branch_id: Optional[int] = None,
             capacity: int = 0, kind: str = 'meeting') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT OR IGNORE INTO rooms(branch_id, name, capacity, kind) '
            'VALUES (?,?,?,?)', (branch_id, name, capacity, kind))
        if cur.lastrowid:
            return cur.lastrowid
        r = c.execute('SELECT id FROM rooms WHERE branch_id IS ? AND name = ?',
                      (branch_id, name)).fetchone()
        return r['id'] if r else 0


def list_rooms() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT r.*, b.name AS branch_name FROM rooms r '
            'LEFT JOIN branches b ON b.id = r.branch_id '
            'ORDER BY b.name, r.name').fetchall()]


def log_occupancy(room_id: int, head_count: int) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO occupancy_samples(room_id, head_count, seen_at) '
            'VALUES (?,?,?)',
            (room_id, head_count,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def occupancy_today(room_id: int) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT seen_at, head_count FROM occupancy_samples "
            "WHERE room_id = ? AND date(seen_at) = date('now') "
            "ORDER BY seen_at", (room_id,)).fetchall()]


# ── N8 Mustering ──────────────────────────────────────────────────────────
def start_muster(name: str, branch_id: Optional[int] = None,
                 safe_zone: str = '') -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO muster_drills(name, branch_id, safe_zone, started_at) '
            'VALUES (?,?,?,?)',
            (name, branch_id, safe_zone or None,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid


def end_muster(drill_id: int) -> None:
    with tx() as c:
        c.execute('UPDATE muster_drills SET ended_at = ? WHERE id = ?',
                  (datetime.now().isoformat(timespec='seconds'), drill_id))


def active_muster() -> Optional[dict]:
    with tx() as c:
        r = c.execute(
            'SELECT * FROM muster_drills WHERE ended_at IS NULL '
            'ORDER BY id DESC LIMIT 1').fetchone()
        return dict(r) if r else None


def list_musters(limit: int = 50) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT m.*, b.name AS branch_name, '
            '(SELECT COUNT(*) FROM muster_checkins WHERE drill_id = m.id) AS checked_in '
            'FROM muster_drills m LEFT JOIN branches b ON b.id = m.branch_id '
            'ORDER BY m.started_at DESC LIMIT ?', (limit,)).fetchall()]


def muster_check_in(drill_id: int, person_id: str) -> bool:
    with tx() as c:
        cur = c.execute(
            'INSERT OR IGNORE INTO muster_checkins(drill_id, person_id, seen_at) '
            'VALUES (?,?,?)',
            (drill_id, person_id,
             datetime.now().isoformat(timespec='seconds')))
        return cur.rowcount > 0


def muster_status(drill_id: int) -> dict:
    with tx() as c:
        drill = c.execute('SELECT * FROM muster_drills WHERE id = ?',
                          (drill_id,)).fetchone()
        if not drill:
            return {}
        # Anyone present today is expected
        present_today = c.execute(
            "SELECT DISTINCT a.person_id, p.name FROM attendance a "
            "JOIN persons p ON p.person_id = a.person_id "
            "WHERE a.date = date(?) AND a.check_in IS NOT NULL",
            (drill['started_at'],)).fetchall()
        checked = {r['person_id'] for r in c.execute(
            'SELECT person_id FROM muster_checkins WHERE drill_id = ?',
            (drill_id,)).fetchall()}
        accounted = [dict(p) for p in present_today if p['person_id'] in checked]
        missing = [dict(p) for p in present_today if p['person_id'] not in checked]
    return {'drill': dict(drill), 'accounted': accounted, 'missing': missing,
            'total_expected': len(present_today)}


# ── N19 SDK clients ───────────────────────────────────────────────────────
def add_sdk_client(name: str, origins: str = '',
                   tenant_id: Optional[int] = None) -> tuple[str, str, int]:
    pub = 'fmsdk_' + secrets.token_urlsafe(8)
    sec = secrets.token_urlsafe(32)
    h = hashlib.sha256(sec.encode()).hexdigest()
    with tx() as c:
        cur = c.execute(
            'INSERT INTO sdk_clients(tenant_id, name, public_id, secret_hash, '
            'origins, created_at) VALUES (?,?,?,?,?,?)',
            (tenant_id, name, pub, h, origins or None,
             datetime.now().isoformat(timespec='seconds')))
        return pub, sec, cur.lastrowid


def verify_sdk_client(public_id: str, secret: str) -> Optional[dict]:
    h = hashlib.sha256(secret.encode()).hexdigest()
    with tx() as c:
        r = c.execute(
            'SELECT * FROM sdk_clients WHERE public_id = ? AND secret_hash = ? AND enabled = 1',
            (public_id, h)).fetchone()
        return dict(r) if r else None


def list_sdk_clients() -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(
            'SELECT id, tenant_id, name, public_id, origins, rate_per_min, enabled, created_at '
            'FROM sdk_clients ORDER BY created_at DESC').fetchall()]


def delete_sdk_client(client_id: int) -> None:
    with tx() as c:
        c.execute('DELETE FROM sdk_clients WHERE id = ?', (client_id,))


def log_sdk_call(client_id: int, endpoint: str, status: str, ms: int = 0) -> int:
    with tx() as c:
        cur = c.execute(
            'INSERT INTO sdk_calls(client_id, endpoint, status, ms, seen_at) '
            'VALUES (?,?,?,?,?)',
            (client_id, endpoint, status, ms,
             datetime.now().isoformat(timespec='seconds')))
        return cur.lastrowid
