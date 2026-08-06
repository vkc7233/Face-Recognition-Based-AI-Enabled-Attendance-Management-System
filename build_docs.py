"""
Build the FaceMark product documentation PDF.

Output: docs/FaceMark-Documentation.pdf
"""

import os
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, KeepTogether, ListFlowable, ListItem,
    HRFlowable, Image,
)

OUT = os.path.join('docs', 'FaceMark-Documentation.pdf')
os.makedirs('docs', exist_ok=True)


# ---------------------------------------------------------------------------
# Style palette
# ---------------------------------------------------------------------------
INDIGO   = HexColor('#4f46e5')
INDIGO_D = HexColor('#3730a3')
EMERALD  = HexColor('#10b981')
AMBER    = HexColor('#f59e0b')
ROSE     = HexColor('#ec4899')
SLATE_9  = HexColor('#0f172a')
SLATE_7  = HexColor('#334155')
SLATE_5  = HexColor('#64748b')
SLATE_3  = HexColor('#cbd5e1')
SLATE_1  = HexColor('#f1f5f9')
SLATE_0  = HexColor('#f8fafc')

styles = getSampleStyleSheet()

# Override / add custom styles
styles.add(ParagraphStyle('Cover',
    fontName='Helvetica-Bold', fontSize=42, leading=46,
    textColor=white, alignment=TA_LEFT, spaceAfter=8))
styles.add(ParagraphStyle('CoverSub',
    fontName='Helvetica', fontSize=15, leading=20,
    textColor=SLATE_1, alignment=TA_LEFT, spaceAfter=2))
styles.add(ParagraphStyle('CoverMeta',
    fontName='Helvetica', fontSize=10, leading=14,
    textColor=SLATE_3, alignment=TA_LEFT))

styles.add(ParagraphStyle('H1',
    fontName='Helvetica-Bold', fontSize=22, leading=26,
    textColor=INDIGO_D, spaceBefore=18, spaceAfter=10))
styles.add(ParagraphStyle('H2',
    fontName='Helvetica-Bold', fontSize=15, leading=19,
    textColor=SLATE_9, spaceBefore=14, spaceAfter=6))
styles.add(ParagraphStyle('H3',
    fontName='Helvetica-Bold', fontSize=12, leading=15,
    textColor=INDIGO, spaceBefore=10, spaceAfter=4))

styles.add(ParagraphStyle('Body',
    fontName='Helvetica', fontSize=10.5, leading=15,
    textColor=SLATE_7, alignment=TA_JUSTIFY, spaceAfter=6))
styles.add(ParagraphStyle('Small',
    fontName='Helvetica', fontSize=9, leading=13,
    textColor=SLATE_5))
styles.add(ParagraphStyle('Mono',
    fontName='Courier', fontSize=9, leading=12,
    textColor=SLATE_9))
styles.add(ParagraphStyle('Lead',
    fontName='Helvetica', fontSize=11.5, leading=17,
    textColor=SLATE_7, alignment=TA_JUSTIFY, spaceAfter=10))

styles.add(ParagraphStyle('FmBullet',
    fontName='Helvetica', fontSize=10.5, leading=14,
    textColor=SLATE_7, leftIndent=14, spaceAfter=3))

styles.add(ParagraphStyle('Caption',
    fontName='Helvetica-Oblique', fontSize=9, leading=12,
    textColor=SLATE_5, alignment=TA_CENTER, spaceAfter=8))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def hr(color=SLATE_3, thickness=0.6):
    return HRFlowable(width='100%', thickness=thickness, color=color,
                      spaceBefore=4, spaceAfter=10)


def kv_table(rows, col_widths=None):
    """Two-column key-value table."""
    t = Table(rows,
              colWidths=col_widths or [4.0 * cm, 12.5 * cm])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9.5),
        ('TEXTCOLOR', (0, 0), (0, -1), INDIGO_D),
        ('TEXTCOLOR', (1, 0), (1, -1), SLATE_7),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [SLATE_0, white]),
        ('BOX', (0, 0), (-1, -1), 0.4, SLATE_3),
        ('LINEBELOW', (0, 0), (-1, -2), 0.3, SLATE_1),
    ]))
    return t


def feature_table(rows, headers, col_widths):
    """Bordered table with header band."""
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), INDIGO_D),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9.5),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('TEXTCOLOR', (0, 1), (-1, -1), SLATE_7),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [SLATE_0, white]),
        ('BOX', (0, 0), (-1, -1), 0.5, SLATE_3),
        ('LINEBELOW', (0, 0), (-1, 0), 1.2, INDIGO_D),
    ]))
    return t


def bullets(items):
    bs = []
    for it in items:
        bs.append(ListItem(Paragraph(it, styles['FmBullet']),
                           leftIndent=12, bulletColor=INDIGO))
    return ListFlowable(bs, bulletType='bullet', start='•',
                        leftIndent=10, spaceBefore=0, spaceAfter=8,
                        bulletFontName='Helvetica-Bold',
                        bulletFontSize=10, bulletColor=INDIGO)


# ---------------------------------------------------------------------------
# Cover page
# ---------------------------------------------------------------------------
def cover_page(canvas, doc):
    canvas.saveState()
    w, h = A4

    # gradient background
    canvas.setFillColor(SLATE_9)
    canvas.rect(0, 0, w, h, fill=1, stroke=0)
    # accent bar
    canvas.setFillColor(INDIGO)
    canvas.rect(0, h - 6, w, 6, fill=1, stroke=0)
    # corner mark
    canvas.setFillColor(INDIGO_D)
    canvas.circle(w + 60, h - 120, 280, fill=1, stroke=0)
    canvas.setFillColor(EMERALD)
    canvas.circle(-40, 100, 220, fill=1, stroke=0)

    # logo block
    canvas.setFillColor(white)
    canvas.roundRect(2.5 * cm, h - 5.0 * cm, 1.6 * cm, 1.6 * cm, 8, fill=1, stroke=0)
    canvas.setFillColor(INDIGO)
    canvas.setFont('Helvetica-Bold', 30)
    canvas.drawCentredString(2.5 * cm + 0.8 * cm, h - 5.0 * cm + 0.42 * cm, 'F')

    # title
    canvas.setFillColor(white)
    canvas.setFont('Helvetica-Bold', 36)
    canvas.drawString(2.5 * cm, h - 6.8 * cm, 'FaceMark')
    canvas.setFont('Helvetica', 18)
    canvas.setFillColor(SLATE_1)
    canvas.drawString(2.5 * cm, h - 7.8 * cm, 'Attendance System')

    # subtitle
    canvas.setFillColor(SLATE_3)
    canvas.setFont('Helvetica', 12)
    canvas.drawString(2.5 * cm, h - 9.4 * cm,
                      'Product documentation — features, architecture, and operations.')

    # meta block at bottom
    canvas.setFillColor(SLATE_5)
    canvas.setFont('Helvetica', 9)
    canvas.drawString(2.5 * cm, 2.5 * cm, f'Build date  ·  {date.today().isoformat()}')
    canvas.drawString(2.5 * cm, 2.0 * cm, 'Backend     ·  Flask + SQLite + OpenCV LBPH')
    canvas.drawString(2.5 * cm, 1.5 * cm, 'Audience    ·  Operators, integrators, resellers')

    canvas.restoreState()


def chapter_header(canvas, doc):
    canvas.saveState()
    w, h = A4
    # top thin band
    canvas.setFillColor(INDIGO_D)
    canvas.rect(0, h - 1.4 * cm, w, 1.4 * cm, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont('Helvetica-Bold', 11)
    canvas.drawString(2 * cm, h - 0.9 * cm, 'FaceMark — Product Documentation')
    canvas.setFont('Helvetica', 9)
    canvas.drawRightString(w - 2 * cm, h - 0.9 * cm, f'Page {doc.page}')
    # bottom footer
    canvas.setFillColor(SLATE_5)
    canvas.setFont('Helvetica', 8)
    canvas.drawString(2 * cm, 1.1 * cm,
                      'Confidential · Internal product reference')
    canvas.drawRightString(w - 2 * cm, 1.1 * cm, 'facemark.local')
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------
story = []

# ────────── TOC ──────────
story.append(Paragraph('Table of contents', styles['H1']))
story.append(hr(INDIGO))

toc = [
    ('1', 'Product overview', '3'),
    ('2', 'Architecture & technology stack', '4'),
    ('3', 'Authentication & roles', '5'),
    ('4', 'Recognition pipeline (LBPH + crowd mode)', '5'),
    ('5', 'Core attendance flow', '7'),
    ('6', 'Academic model — Subjects, Sessions, Defaulters', '8'),
    ('7', 'Unique selling features', '9'),
    ('8', 'All routes (full URL map)', '11'),
    ('9', 'Database schema', '13'),
    ('10', 'Files & folders', '14'),
    ('11', 'Settings reference', '15'),
    ('12', 'Day-to-day workflows', '16'),
    ('13', 'Sales pitch & target buyers', '17'),
    ('14', 'Setup & install guide', '17'),
    ('15', 'Future roadmap', '18'),
]
toc_rows = [[n, Paragraph(t, styles['Body']),
             Paragraph(f'<font color="#64748b">p. {p}</font>', styles['Body'])]
            for n, t, p in toc]
toc_table = Table(toc_rows, colWidths=[1.0 * cm, 13.0 * cm, 2.2 * cm])
toc_table.setStyle(TableStyle([
    ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
    ('TEXTCOLOR', (0, 0), (0, -1), INDIGO_D),
    ('FONTSIZE', (0, 0), (-1, -1), 10.5),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('TOPPADDING', (0, 0), (-1, -1), 7),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ('LINEBELOW', (0, 0), (-1, -1), 0.3, SLATE_1),
]))
story.append(toc_table)
story.append(PageBreak())


# ────────── 1. Product overview ──────────
story.append(Paragraph('1. Product overview', styles['H1']))
story.append(hr())

story.append(Paragraph(
    'FaceMark is a self-hosted, single-binary attendance platform built around '
    'face recognition. It runs on any PC with a webcam, requires no cloud account, '
    'and ships with every feature a school, college, or office needs out of the '
    'box — from in-browser live recognition to printable ID cards, a tablet '
    'kiosk mode, a hallway TV display, automated visitor logging, and the 75% '
    'defaulter rule that Indian colleges live by.',
    styles['Lead']))

story.append(Paragraph('What problem does it solve?', styles['H2']))
story.append(Paragraph(
    'Manual attendance is slow, error-prone, easy to proxy, and impossible to '
    'audit. Commodity biometric devices charge per-user license fees, lock data '
    'behind proprietary apps, and require expensive hardware. FaceMark replaces '
    'all of that with a single Flask app that runs on the school’s own machine.',
    styles['Body']))

story.append(Paragraph('Who is it for?', styles['H2']))
story.append(bullets([
    '<b>Schools &amp; colleges</b> — class-period attendance, defaulter '
    'reports, printable ID cards, guardian email exports',
    '<b>Corporate offices</b> — employee check-in / check-out, late-arrival '
    'flags, work-hour tracking',
    '<b>Coworking spaces, gyms, clinics</b> — member recognition, visitor '
    'security log',
    '<b>Government &amp; PSU offices</b> — fully on-premise, no internet '
    'required after install',
]))

story.append(Paragraph('Key differentiators at a glance', styles['H2']))
story.append(feature_table(
    rows=[
        ['Recognition backend', 'OpenCV LBPH (contrib) with face alignment, '
                                'data augmentation, 3-of-5 vote confirmation'],
        ['Crowd mode', 'Recognises every face in the frame simultaneously, '
                       'per-identity voting, capture flash'],
        ['Kiosk mode', 'Full-screen tablet UI with sound feedback for the '
                       'school gate or office reception'],
        ['Big-screen display', 'Projector / TV view with live counts and '
                               'latest arrivals'],
        ['Visitor log', 'Auto-snapshots every unknown face for security audit'],
        ['ID cards', 'Printable cards with QR codes that link back to the '
                     'student profile'],
        ['Smart insights', 'Auto-detects late arrivals, attendance drops, '
                           'birthdays, unknown faces'],
        ['Defaulter rule', 'Configurable per-subject percentage threshold '
                           '(default 75%)'],
        ['Heatmap', 'GitHub-style 90-day grid per user'],
        ['On-premise', 'SQLite, no cloud, no per-user fees, no internet needed'],
    ],
    headers=['Capability', 'What it does'],
    col_widths=[5.0 * cm, 11.5 * cm],
))
story.append(PageBreak())


# ────────── 2. Architecture ──────────
story.append(Paragraph('2. Architecture & technology stack', styles['H1']))
story.append(hr())

story.append(Paragraph(
    'FaceMark is a monolithic Flask application that owns its own SQLite '
    'database and its own static asset directory. The browser is the only '
    'client; there is no separate React / mobile app to deploy.',
    styles['Body']))

story.append(Paragraph('Tech stack', styles['H2']))
story.append(kv_table([
    ['Web framework', 'Flask 3 — sessions, jinja2 templates, MJPEG streaming'],
    ['Database', 'SQLite (single file, facemark.db) with foreign keys enabled'],
    ['Face detection', 'OpenCV Haar cascade with histogram equalisation'],
    ['Face recognition', 'OpenCV LBPH (opencv-contrib-python) with KNN fallback'],
    ['ML preprocessing', 'Eye alignment, blur rejection, data augmentation'],
    ['Charts', 'Chart.js (CDN) for analytics page'],
    ['QR codes', 'qrcode.js (CDN) for ID-card generation'],
    ['Excel export', 'openpyxl via pandas ExcelWriter'],
    ['Password hashing', 'werkzeug.security (PBKDF2-SHA256)'],
    ['Styling', 'Bootstrap 5 base + custom CSS (~750 lines)'],
]))

story.append(Paragraph('High-level data flow', styles['H2']))
story.append(Paragraph(
    'A browser opens the Flask app. The MJPEG stream at <font face="Courier">'
    '/video_feed</font> is served from the same Flask process that holds the '
    'OpenCV VideoCapture handle, so there is no separate inference service '
    'to orchestrate. The recogniser writes attendance directly into SQLite. '
    'Every dashboard panel polls a JSON API (<font face="Courier">/api/stats</font>, '
    '<font face="Courier">/api/recent</font>, <font face="Courier">/api/just_captured</font>, '
    '<font face="Courier">/api/insights</font>, <font face="Courier">/api/heatmap</font>) '
    'so the UI updates within ~1 second of a recognition event without a page '
    'refresh.',
    styles['Body']))

story.append(Paragraph('Module map', styles['H2']))
story.append(kv_table([
    ['app.py', 'Flask routes, auth, streaming pipeline, voting buffer, capture flash'],
    ['db.py', 'SQLite schema, migrations, all CRUD + analytics queries'],
    ['recognizer.py', 'LBPH/KNN abstraction with train/predict/is_trained API'],
    ['face_utils.py', 'Haar detection, eye-based alignment, augmentation, blur gate'],
    ['templates/', '15 Jinja2 templates including the kiosk and display screens'],
    ['static/css/styles.css', 'Custom design system (~750 lines)'],
    ['static/faces/', 'Captured face crops per user (training data)'],
    ['static/profiles/', '160x160 profile thumbnails extracted at enrollment'],
    ['static/visitors/', 'Auto-snapshots of unknown faces'],
    ['Attendance/', 'CSV files (export-only, kept for backward compatibility)'],
]))
story.append(PageBreak())


# ────────── 3. Auth ──────────
story.append(Paragraph('3. Authentication & roles', styles['H1']))
story.append(hr())
story.append(Paragraph(
    'Every route except <font face="Courier">/login</font> is protected by a '
    '<font face="Courier">@login_required</font> decorator. Sessions use '
    'Flask’s signed-cookie store with a configurable secret '
    '(<font face="Courier">FACEMARK_SECRET</font> environment variable).',
    styles['Body']))
story.append(Paragraph('Default credentials', styles['H3']))
story.append(Paragraph(
    'On first run the database seeds a single admin: '
    '<font face="Courier" color="#4f46e5">admin / admin123</font>. '
    'The Settings page forces a password change in production. Passwords are '
    'stored as PBKDF2-SHA256 hashes via werkzeug.security, never plain text.',
    styles['Body']))
story.append(Paragraph('Audit log', styles['H3']))
story.append(Paragraph(
    'Every admin action is recorded in the <font face="Courier">audit_log</font> '
    'table: login, logout, add/edit/delete user, add/delete subject, create/delete '
    'session, set active session, update settings, clear visitor log, manual mark, '
    'bulk import. Visible at <font face="Courier">/audit</font>.',
    styles['Body']))


# ────────── 4. Recognition pipeline ──────────
story.append(Paragraph(
    '4. Recognition pipeline (LBPH + crowd mode)', styles['H1']))
story.append(hr())

story.append(Paragraph(
    'The pipeline is engineered for accuracy in real-world conditions — '
    'mixed lighting, multiple faces, partial occlusion, motion.',
    styles['Body']))

story.append(Paragraph('Step 1 — detection', styles['H3']))
story.append(bullets([
    'Haar cascade with <font face="Courier">scaleFactor=1.15</font>, '
    '<font face="Courier">minNeighbors=6</font>, <font face="Courier">minSize=50&#215;50</font> '
    '(small enough for the back of a classroom)',
    'Histogram equalisation runs before detection — robust to backlit windows '
    'and dim corridors',
]))

story.append(Paragraph('Step 2 — preprocessing', styles['H3']))
story.append(bullets([
    'Eye-based alignment: a second Haar cascade locates eyes, the crop is '
    'rotated so the eye line is horizontal',
    'Resized to 200&#215;200, converted to grayscale, histogram-equalised',
    'Blur gate at enrolment: Laplacian variance must exceed 60 or the frame is '
    'rejected. The kiosk shows the live <i>Sharpness</i> number so users know '
    'when to hold still',
]))

story.append(Paragraph('Step 3 — enrolment & training', styles['H3']))
story.append(bullets([
    '25 sharp samples per user',
    'Each sample is augmented to 5 variants (flip + brightness &#177;20 + '
    'rotation &#177;5°) before training',
    'LBPH parameters: <font face="Courier">radius=1, neighbors=8, grid_x=8, grid_y=8</font>',
    'Auto-fallback to scikit-learn KNN if <font face="Courier">cv2.face</font> '
    'is unavailable',
]))

story.append(Paragraph('Step 4 — crowd-mode voting', styles['H3']))
story.append(Paragraph(
    'For every frame, the pipeline predicts every detected face independently. '
    'Each accepted prediction is appended to a per-identity ring buffer with a '
    'timestamp. A 2.5-second sliding window prunes stale votes. An identity is '
    'confirmed only when it accumulates <b>3 hits inside that window</b>, after '
    'which an 8-second cooldown prevents re-marking. This makes the system '
    'robust to false positives even with 10+ people on screen.',
    styles['Body']))

story.append(Paragraph('Step 5 — capture flash', styles['H3']))
story.append(Paragraph(
    'For 1.5 seconds after marking, the confirmed face gets a green '
    '✓ OK circle, a CAPTURED label, and a darker green bounding box. The '
    'browser also fires a non-blocking toast — critical visual feedback so '
    'users at the kiosk know they’re done.',
    styles['Body']))

story.append(Paragraph('Step 6 — unknown face logging', styles['H3']))
story.append(Paragraph(
    'Any face whose nearest LBPH distance exceeds the configurable threshold '
    '(default 80) is labelled <i>Unknown</i> and a 200&#215;200 snapshot is '
    'written to <font face="Courier">static/visitors/</font>. A 30-second '
    'cooldown prevents storage flooding.',
    styles['Body']))
story.append(PageBreak())


# ────────── 5. Core attendance flow ──────────
story.append(Paragraph('5. Core attendance flow', styles['H1']))
story.append(hr())

story.append(Paragraph(
    'Attendance is automatic, idempotent, and bidirectional — first '
    'sighting of the day is recorded as a check-in, last sighting (after the '
    'configured minimum gap) is recorded as a check-out.',
    styles['Body']))

story.append(Paragraph('Check-in', styles['H3']))
story.append(bullets([
    'First confirmed sighting of the day for a person inserts a row into '
    '<font face="Courier">attendance</font>',
    'Compared against <font face="Courier">work_start_time + late_threshold_min</font>; '
    'past that, <font face="Courier">is_late = 1</font>',
    'Tagged with the current session id if a class session is active',
]))

story.append(Paragraph('Check-out', styles['H3']))
story.append(bullets([
    'A subsequent sighting of the same person, at least '
    '<font face="Courier">min_checkout_gap_min</font> minutes after their check-in, '
    'updates the <font face="Courier">check_out</font> column',
    'Allows the daily report to show real work hours / class hours',
]))

story.append(Paragraph('Late-arrival flagging', styles['H3']))
story.append(Paragraph(
    'Configurable in Settings. The dashboard “Late today” KPI, the '
    'history page status chip, the heatmap colour (amber), the smart-insights '
    'strip, and the big-screen display all use this single flag.',
    styles['Body']))

story.append(Paragraph('Manual mark / override', styles['H3']))
story.append(Paragraph(
    'Camera missing? Sick student excused? Every user list shows a green '
    'check button for instant manual check-in, and every user-detail page has '
    'explicit <i>Check in</i> and <i>Check out</i> buttons. Manual marks are '
    'recorded in the audit log with the admin’s username.',
    styles['Body']))

story.append(Paragraph('Yet-to-arrive panel', styles['H3']))
story.append(Paragraph(
    'A live panel on the dashboard shows every registered user with no '
    'check-in row for today. One-click manual mark on each row — useful '
    'when a few students arrive while the camera is busy, or for late '
    'reconciliation at end of day.',
    styles['Body']))

story.append(Paragraph('Live feed', styles['H3']))
story.append(Paragraph(
    'A separate panel polls <font face="Courier">/api/recent</font> every 3 '
    'seconds and shows the eight most recent sightings with profile '
    'thumbnails, time, and late/on-time chip. Updates without page refresh.',
    styles['Body']))
story.append(PageBreak())


# ────────── 6. Academic model ──────────
story.append(Paragraph('6. Academic model — Subjects, Sessions, Defaulters',
                       styles['H1']))
story.append(hr())

story.append(Paragraph(
    'A flat daily attendance is enough for an office, but colleges need '
    'period-level granularity. FaceMark supports that without disturbing the '
    'simple daily flow.',
    styles['Body']))

story.append(Paragraph('Subjects', styles['H3']))
story.append(Paragraph(
    'Stored at <font face="Courier">/subjects</font>. Each subject has a '
    'name, an optional code (e.g. <font face="Courier">CS101</font>), and an '
    'optional department/class link.',
    styles['Body']))

story.append(Paragraph('Class sessions', styles['H3']))
story.append(Paragraph(
    'A session is a single class period: <i>subject + date + start_time + '
    'end_time + notes</i>. Created at <font face="Courier">/sessions</font>. '
    'Sessions can be created on the fly with the <i>Activate immediately</i> '
    'checkbox — typical workflow for a teacher walking into class.',
    styles['Body']))

story.append(Paragraph('Active session', styles['H3']))
story.append(Paragraph(
    'At most one session can be “active” at a time. While active, '
    'every confirmed recognition writes <i>two</i> rows: the daily check-in '
    '(<font face="Courier">attendance</font>) <i>and</i> the period attendance '
    '(<font face="Courier">session_attendance</font>). The dashboard shows a '
    'pulsing green banner with <i>End session</i> button so it’s obvious '
    'when class is in session.',
    styles['Body']))

story.append(Paragraph('Defaulter report', styles['H3']))
story.append(Paragraph(
    'At <font face="Courier">/defaulters</font>, FaceMark computes per-student '
    'per-subject attendance percentages and lists everyone below the '
    'configurable threshold (default 75%). Colour-coded pills (red below '
    '50%, amber below threshold) make it easy to act on. The threshold is '
    'configurable on the page itself or in Settings.',
    styles['Body']))

story.append(Paragraph('Printable register', styles['H3']))
story.append(Paragraph(
    'At <font face="Courier">/register/print</font>, FaceMark renders a '
    'print-friendly attendance register for any date — daily check-ins, '
    'sessions held, signature lines for the faculty and HOD, page numbers, '
    'and auto-trigger of the browser print dialog. Exactly what colleges hand '
    'to inspectors and university auditors.',
    styles['Body']))
story.append(PageBreak())


# ────────── 7. Unique selling features ──────────
story.append(Paragraph('7. Unique selling features', styles['H1']))
story.append(hr())

story.append(Paragraph(
    'These are the features that turn FaceMark from “a face-recognition '
    'attendance system” into <b>a product you can sell at a premium</b>.',
    styles['Body']))

story.append(Paragraph('Kiosk mode — the gate terminal', styles['H2']))
story.append(Paragraph(
    'Available at <font face="Courier">/kiosk</font>. Full-screen, '
    'no-chrome, sound-on. Auto-starts the recognition pipeline, plays a '
    'two-tone chime when someone is marked, shows their profile photo + name '
    '+ class for ~4 seconds in a giant card, and keeps a live clock and '
    'present/late counters in the corners. Designed to run unattended on a '
    '₹15,000 tablet mounted at the school gate or office reception.',
    styles['Body']))

story.append(Paragraph('Big-screen display — the hallway TV', styles['H2']))
story.append(Paragraph(
    'Available at <font face="Courier">/display</font>. Designed for a '
    'TV in the hallway or a projector at assembly. Shows gigantic counters '
    '(Present today, Attendance rate), the live clock, and a “Latest '
    'arrivals” list with profile photos. Refreshes every 3 seconds with '
    'no user input. Strong marketing visual for school open days and parent '
    'visits.',
    styles['Body']))

story.append(Paragraph('Visitor log — entrance security', styles['H2']))
story.append(Paragraph(
    'Available at <font face="Courier">/visitors</font>. Every face '
    'detected that does not match anyone in the database is silently saved '
    'as a 200&#215;200 snapshot with timestamp and camera name. Schools and '
    'offices need this for safety compliance (CCTV-like log) without paying '
    'for separate surveillance software. One-click <i>Clear log</i> deletes '
    'all snapshots from disk to keep storage in check.',
    styles['Body']))

story.append(Paragraph('Smart insights — zero-effort alerting',
                       styles['H2']))
story.append(Paragraph(
    'A strip of automatic alerts at the top of the dashboard. The engine '
    'detects, every load:',
    styles['Body']))
story.append(bullets([
    '<b>Late arrivals today</b> — count + link to today’s view',
    '<b>Yet to arrive</b> — registered users with no check-in today',
    '<b>Unknown faces in last 24 h</b> — link to visitor log',
    '<b>Attendance drop</b> — per-user: this week vs last week, '
    'flagged if recent &lt; previous &#8722; 1',
    '<b>Birthdays today</b> — deduced from <font face="Courier">date_of_birth</font>',
]))

story.append(Paragraph('Printable ID cards — a revenue line',
                       styles['H2']))
story.append(Paragraph(
    'Available at <font face="Courier">/idcard/&lt;roll&gt;</font>. A 340&#215;540 '
    'pixel card with org-branded banner, profile photo, roll number, '
    'department, email, date of birth, enrolment date, and a QR code that '
    'links back to the student’s profile page. One-click print. Schools '
    'sell ID cards every year — FaceMark turns that into a built-in '
    'feature instead of a printer-vendor headache.',
    styles['Body']))

story.append(Paragraph('GitHub-style 90-day heatmap', styles['H2']))
story.append(Paragraph(
    'On every user profile, a 30-column grid shows the last 90 days of '
    'attendance. Green = on time, amber = late, grey = absent. Instant '
    'visual demo wow-factor; parents and inspectors understand it without '
    'explanation.',
    styles['Body']))

story.append(Paragraph('Birthday widget', styles['H2']))
story.append(Paragraph(
    'Today’s birthdays appear in the smart-insights strip with a cake '
    'icon and a link to the user’s profile. Drives daily admin login and '
    'parent engagement.',
    styles['Body']))
story.append(PageBreak())


# ────────── 8. All routes ──────────
story.append(Paragraph('8. All routes (full URL map)', styles['H1']))
story.append(hr())
story.append(Paragraph(
    'Every URL the app responds to, grouped by category.',
    styles['Body']))

route_groups = [
    ('Auth & shell', [
        ['GET /login', 'Login screen'],
        ['POST /login', 'Validate credentials, set session'],
        ['GET /logout', 'Clear session, return to login'],
        ['GET /', 'Dashboard'],
    ]),
    ('Recognition stream', [
        ['GET /video_feed', 'MJPEG live stream'],
        ['POST /start_recognise', 'Switch streaming pipeline to recognise mode'],
        ['POST /stop_capture', 'Reset pipeline to idle'],
        ['GET /capture_status', 'JSON: current capture progress'],
    ]),
    ('Users', [
        ['POST /add', 'Begin enrolment + start capture'],
        ['GET /register_capture', 'Capture page with sharpness bar'],
        ['GET /listusers', 'Filterable user list'],
        ['POST /edituser', 'Update name / dept / email / DOB'],
        ['GET /deleteuser', 'Delete user, retrain model'],
        ['POST /import_users', 'Bulk CSV import'],
        ['GET /user/&lt;pid&gt;', 'Profile + heatmap + history'],
    ]),
    ('Manual override', [
        ['POST /manual_mark', 'Force check-in or check-out'],
    ]),
    ('History & exports', [
        ['GET /history', 'Pick any date and view its attendance'],
        ['GET /download', 'CSV or .xlsx download (?fmt=xlsx)'],
        ['GET /register/print', 'Print-ready attendance register'],
        ['GET /contacts.csv', 'Bulk contact export (incl. guardian email)'],
    ]),
    ('Academic model', [
        ['GET/POST /subjects', 'List + add + delete subjects'],
        ['GET/POST /sessions', 'List + add + delete class sessions'],
        ['GET /session/&lt;sid&gt;', 'Per-session attendance roster'],
        ['POST /set_active_session', 'Activate or clear active session'],
        ['GET /defaulters', 'Students below threshold percentage'],
    ]),
    ('Operate (kiosk & display)', [
        ['GET /kiosk', 'Full-screen tablet kiosk'],
        ['GET /display', 'Big-screen hallway display'],
        ['GET /visitors', 'Unknown-face log'],
        ['POST /visitors/clear', 'Wipe visitor log + snapshots'],
        ['GET /idcard/&lt;pid&gt;', 'Printable ID card with QR'],
    ]),
    ('Reports', [
        ['GET /analytics', 'Chart.js dashboards: trend + late vs on-time + top attenders'],
    ]),
    ('Configuration', [
        ['GET/POST /settings', 'Org, working hours, recognition threshold, '
                              'departments, admin password'],
        ['GET /audit', 'Audit log viewer'],
    ]),
    ('JSON APIs', [
        ['GET /api/stats', 'Counts for dashboards'],
        ['GET /api/recent', 'Last 8 sightings today'],
        ['GET /api/just_captured', 'Live capture-flash events'],
        ['GET /api/insights', 'Smart-insights payload'],
        ['GET /api/heatmap/&lt;pid&gt;', '90-day attendance grid'],
    ]),
]

for label, rows in route_groups:
    story.append(Paragraph(label, styles['H3']))
    paragraph_rows = [
        [Paragraph(f'<font face="Courier" color="#3730a3">{r[0]}</font>',
                   styles['Small']),
         Paragraph(r[1], styles['Small'])]
        for r in rows
    ]
    t = Table(paragraph_rows, colWidths=[6.5 * cm, 10.0 * cm])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [SLATE_0, white]),
        ('BOX', (0, 0), (-1, -1), 0.3, SLATE_3),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))

story.append(PageBreak())


# ────────── 9. DB schema ──────────
story.append(Paragraph('9. Database schema', styles['H1']))
story.append(hr())
story.append(Paragraph(
    'SQLite is the single source of truth. Every CSV / Excel export is '
    'generated on demand from these tables. Lightweight migrations run in '
    '<font face="Courier">db.init_db()</font> so future schema bumps are '
    'non-destructive.',
    styles['Body']))

schema_rows = [
    ['admin_users',     'id, username (unique), password_hash, role, created_at'],
    ['departments',     'id, name (unique). Used as classes for schools.'],
    ['persons',         'id, person_id (roll/employee), name, department_id, '
                        'email, guardian_email, date_of_birth, created_at'],
    ['attendance',      'id, person_id, date, check_in, check_out, is_late, '
                        'UNIQUE(person_id, date)'],
    ['settings',        'key (PK), value. Stores org_name, work_start_time, '
                        'late_threshold_min, recognition_threshold, '
                        'attendance_required_pct, active_session_id, ...'],
    ['audit_log',       'id, actor, action, detail, created_at (indexed DESC)'],
    ['subjects',        'id, name, code, department_id, UNIQUE(name, department_id)'],
    ['class_sessions',  'id, subject_id, date, start_time, end_time, notes, '
                        'created_at (date indexed)'],
    ['session_attendance', 'id, session_id, person_id, marked_at, '
                           'UNIQUE(session_id, person_id)'],
    ['visitors',        'id, snapshot, seen_at (indexed DESC), camera'],
]
story.append(feature_table(
    rows=schema_rows,
    headers=['Table', 'Columns'],
    col_widths=[4.5 * cm, 12.0 * cm],
))

story.append(Paragraph('Migrations', styles['H2']))
story.append(Paragraph(
    'Two safe <font face="Courier">ALTER TABLE</font>s run idempotently on '
    'startup:',
    styles['Body']))
story.append(bullets([
    'Add <font face="Courier">persons.guardian_email</font> if missing',
    'Add <font face="Courier">persons.date_of_birth</font> if missing',
]))
story.append(PageBreak())


# ────────── 10. Files ──────────
story.append(Paragraph('10. Files & folders', styles['H1']))
story.append(hr())

story.append(feature_table(
    rows=[
        ['app.py', 'Flask routes + recognition streaming + voting + auth'],
        ['db.py', 'SQLite schema + 30+ helper functions'],
        ['recognizer.py', 'LBPH/KNN backend abstraction'],
        ['face_utils.py', 'Detection, alignment, augmentation, blur gate'],
        ['build_docs.py', 'This documentation generator'],
        ['requirements.txt', 'Flask, opencv-contrib-python, scikit-learn, '
                             'joblib, pandas, openpyxl, werkzeug'],
        ['facemark.db', 'SQLite database (auto-created at first run)'],
        ['haarcascade_frontalface_default.xml', 'OpenCV face cascade'],
        ['templates/', '15 Jinja2 templates'],
        ['static/css/styles.css', 'Custom design system'],
        ['static/faces/&lt;Name_RollNo&gt;/', 'Face crops captured at enrolment'],
        ['static/profiles/&lt;RollNo&gt;.jpg', '160x160 profile thumbnails'],
        ['static/visitors/visitor_&lt;ts&gt;.jpg', 'Unknown-face snapshots'],
        ['static/lbph_model.yml', 'Trained LBPH model'],
        ['static/lbph_labels.json', 'Label ↔ user mapping'],
        ['Attendance/Attendance-&lt;tag&gt;.csv', 'Legacy CSVs (export-only path)'],
        ['docs/FaceMark-Documentation.pdf', 'This document'],
    ],
    headers=['Path', 'Purpose'],
    col_widths=[7.5 * cm, 9.0 * cm],
))

story.append(Paragraph('Template inventory', styles['H2']))
story.append(feature_table(
    rows=[
        ['base.html', 'Sidebar shell + topbar + toast deck'],
        ['login.html', 'Standalone login screen'],
        ['home.html', 'Dashboard: insights, KPIs, live recognition, register form, '
                      'today’s attendance, live feed, yet-to-arrive'],
        ['listusers.html', 'Filterable user list with edit modal + bulk import'],
        ['register.html', 'Live capture page with sharpness bar'],
        ['history.html', 'Date-picker history + CSV / Excel / print'],
        ['analytics.html', 'Chart.js trend + late vs on-time + top attenders'],
        ['settings.html', 'Org, working hours, recognition, departments, password'],
        ['audit.html', 'Audit log viewer'],
        ['subjects.html', 'Subject CRUD'],
        ['sessions.html', 'Class-session CRUD with Activate-now option'],
        ['session_detail.html', 'Per-session attendance roster'],
        ['defaulters.html', 'Below-threshold student list'],
        ['user_detail.html', 'Profile + KPIs + heatmap + history + ID card link'],
        ['visitors.html', 'Unknown-face snapshot grid'],
        ['register_print.html', 'Print-ready attendance register'],
        ['kiosk.html', 'Full-screen tablet kiosk with audio'],
        ['display.html', 'Big-screen hallway display'],
        ['idcard.html', 'Printable ID card with QR code'],
    ],
    headers=['Template', 'Purpose'],
    col_widths=[5.0 * cm, 11.5 * cm],
))
story.append(PageBreak())


# ────────── 11. Settings reference ──────────
story.append(Paragraph('11. Settings reference', styles['H1']))
story.append(hr())
story.append(Paragraph(
    'All settings live in the <font face="Courier">settings</font> table and '
    'can be edited at <font face="Courier">/settings</font>.',
    styles['Body']))

story.append(feature_table(
    rows=[
        ['org_name', 'Displayed in sidebar, login screen, footer, '
                     'ID cards. Default: <i>FaceMark Attendance</i>.'],
        ['work_start_time', 'HH:MM. Default <i>09:00</i>. Anything past '
                            'this plus grace minutes is flagged late.'],
        ['late_threshold_min', 'Minutes of grace after work_start. Default <i>15</i>.'],
        ['min_checkout_gap_min', 'Minimum minutes between a check-in and a '
                                 'check-out for the same person. Default <i>30</i>.'],
        ['recognition_threshold', 'LBPH distance cutoff. Lower = stricter. '
                                  'Default <i>80</i> (LBPH); use <i>7000</i> with KNN.'],
        ['attendance_required_pct', 'Percentage threshold for defaulter '
                                    'report. Default <i>75</i> (Indian college rule).'],
        ['active_session_id', 'Currently-running class session id. Empty '
                              'string means daily-attendance mode.'],
        ['logo_filename', 'Optional uploaded organisation logo.'],
    ],
    headers=['Key', 'Description & default'],
    col_widths=[5.5 * cm, 11.0 * cm],
))

story.append(Paragraph('Tuning recognition for your environment', styles['H2']))
story.append(bullets([
    '<b>Bright outdoor light + multiple similar-looking users:</b> drop '
    'threshold to 60–70 to avoid mistakes',
    '<b>Dim indoor light, very few users:</b> raise threshold to 90–95 '
    'so the system still recognises them',
    '<b>Want fewer late flags?</b> Raise <i>late_threshold_min</i> to 30',
    '<b>Want shorter check-out gap?</b> Drop <i>min_checkout_gap_min</i> to 5 '
    '— useful in a gym where members enter and leave quickly',
]))
story.append(PageBreak())


# ────────── 12. Workflows ──────────
story.append(Paragraph('12. Day-to-day workflows', styles['H1']))
story.append(hr())

story.append(Paragraph('A. First-time setup (10 minutes)', styles['H2']))
story.append(bullets([
    'Install: <font face="Courier">pip install -r requirements.txt</font>',
    'Launch: <font face="Courier">python app.py</font>',
    'Browse to <font face="Courier">http://localhost:5000/</font>; sign in '
    'with <font face="Courier">admin / admin123</font>',
    'Settings → change admin password, set org name and work hours',
    'Settings → add classes / departments (e.g. <i>B.Tech CSE Sem 4</i>)',
    'Subjects → add subjects with codes (e.g. <i>CS101 Data Structures</i>)',
    'Users → bulk-import students via CSV (name, id, department, email, '
    'guardian_email)',
    'Dashboard → register the first face to train the model',
]))

story.append(Paragraph('B. Daily teacher workflow', styles['H2']))
story.append(bullets([
    'Open dashboard → Sessions → New session with '
    '<i>Activate immediately</i> checked',
    'Click Start on live recognition; students walk in, get marked',
    'Click <i>End session</i> when class is over',
    'Per-session attendance is now visible at <font face="Courier">/session/&lt;id&gt;</font>',
]))

story.append(Paragraph('C. Daily reception / gate workflow', styles['H2']))
story.append(bullets([
    'Power on tablet at the gate; browser opens to '
    '<font face="Courier">/kiosk</font> in fullscreen',
    'Recognition starts automatically; chime on every mark',
    'No human attendant required',
]))

story.append(Paragraph('D. End-of-day admin workflow', styles['H2']))
story.append(bullets([
    'History → today’s date → Excel / Print',
    'Defaulters → export below-threshold list',
    'Visitor log → review unknown faces from the day; clear if all benign',
]))

story.append(Paragraph('E. End-of-term reporting workflow', styles['H2']))
story.append(bullets([
    'Analytics → Last 30 days view',
    'Defaulters → set threshold to 75% → download list',
    'Per-student: <font face="Courier">/user/&lt;roll&gt;</font> → prints '
    'easily for parent meetings',
    'ID card renewals: <font face="Courier">/idcard/&lt;roll&gt;</font> for '
    'each student → print to card stock',
]))
story.append(PageBreak())


# ────────── 13. Sales pitch ──────────
story.append(Paragraph('13. Sales pitch & target buyers', styles['H1']))
story.append(hr())

story.append(Paragraph(
    'A short one-liner that works in cold emails, demos, and proposals:',
    styles['Body']))
story.append(Paragraph(
    '“<i>FaceMark is the only attendance system that ships with everything '
    'a school needs out of the box — a tablet kiosk for the gate, a hallway '
    'TV display, automatic visitor logging, printable ID cards, smart insights '
    'that flag attendance drops before parents complain, and the 75% defaulter '
    'rule with one-click guardian email export. Setup is under ten minutes, '
    'no cloud account needed, works on a single PC.</i>”',
    styles['Lead']))

story.append(Paragraph('Comparison vs. typical competitors', styles['H2']))
story.append(feature_table(
    rows=[
        ['Per-user license fees',            'Yes',  'No — unlimited users'],
        ['Cloud required',                   'Often', 'No — fully on-premise'],
        ['Multi-face / crowd recognition',   'Few',   'Yes'],
        ['Kiosk + TV display included',      'Rare',  'Yes'],
        ['Visitor log',                      'Add-on', 'Built-in'],
        ['ID card generator',                'Vendor lock-in', 'Built-in PDF/print'],
        ['Per-class period attendance',      'Rare',  'Yes'],
        ['75% defaulter rule',               'Manual',  'One-click'],
        ['Heatmap visualisation',            'No',    'Yes'],
        ['Audit log for compliance',         'Sometimes', 'Yes, every action'],
    ],
    headers=['Capability', 'Typical competitor', 'FaceMark'],
    col_widths=[5.5 * cm, 5.5 * cm, 5.5 * cm],
))

story.append(Paragraph('Recommended target buyers', styles['H2']))
story.append(bullets([
    '<b>Private schools (CBSE/ICSE)</b> — willing to pay for parent '
    'engagement features; ID card revenue line',
    '<b>Engineering colleges</b> — 75% defaulter rule, per-subject '
    'attendance, university-grade printable register',
    '<b>Coaching institutes</b> — batch-wise sessions, late tracking, '
    'guardian email integration',
    '<b>Corporate offices</b> — employee check-in/out, work-hour audit, '
    'visitor security log',
    '<b>Government &amp; PSU offices</b> — on-premise requirement; no '
    'cloud dependency',
    '<b>Gyms &amp; coworking spaces</b> — member recognition; visitor log '
    'for safety',
]))


# ────────── 14. Setup guide ──────────
story.append(Paragraph('14. Setup & install guide', styles['H1']))
story.append(hr())

story.append(Paragraph('Hardware', styles['H2']))
story.append(bullets([
    'Any Windows / macOS / Linux PC with 4 GB RAM and a webcam',
    'Optional: Android tablet for kiosk mode (any browser)',
    'Optional: TV for the big-screen display (HDMI from the PC)',
]))

story.append(Paragraph('Software prerequisites', styles['H2']))
story.append(bullets([
    'Python 3.10 or newer',
    'pip + a working internet connection for the initial install only',
]))

story.append(Paragraph('Install steps', styles['H2']))
story.append(Paragraph(
    '<font face="Courier" color="#3730a3">'
    'pip install -r requirements.txt<br/>'
    'python app.py<br/>'
    'open http://localhost:5000/  ; login admin / admin123'
    '</font>',
    styles['Body']))

story.append(Paragraph('Production deployment notes', styles['H2']))
story.append(bullets([
    'Replace the Flask dev server with gunicorn or waitress',
    'Place behind nginx with HTTPS terminated at the edge',
    'Set <font face="Courier">FACEMARK_SECRET</font> env var to a strong '
    'random value before first boot',
    'Schedule nightly backups of <font face="Courier">facemark.db</font> and '
    '<font face="Courier">static/faces/</font>',
    'Restrict the kiosk URL to a single IP if the tablet is on the same LAN',
]))
story.append(PageBreak())


# ────────── 15. Future roadmap ──────────
story.append(Paragraph('15. Future roadmap', styles['H1']))
story.append(hr())

story.append(Paragraph(
    'Features the codebase is already structured to support, in priority order:',
    styles['Body']))

story.append(feature_table(
    rows=[
        ['Eye-blink anti-spoofing',
         'Detect liveness before marking; defeats printed-photo attacks. '
         'Hooks into <font face="Courier">face_utils.py</font>.'],
        ['WhatsApp / SMS guardian alerts',
         'Daily attendance and absentee push notifications. '
         '<font face="Courier">guardian_email</font> field is already in DB.'],
        ['Mobile companion PWA',
         'Parents see their child’s heatmap and history. The JSON APIs '
         'are already in place.'],
        ['Multi-tenant SaaS mode',
         'Subdomain-per-school, central billing. Database is per-org-keyed today.'],
        ['Recurring weekly timetable',
         'Schedule Monday–Friday class slots once; sessions auto-create.'],
        ['Excused leave / leave requests',
         'A new <font face="Courier">leaves</font> table + approval flow.'],
        ['Dockerfile + gunicorn + nginx',
         'One-command production deployment.'],
        ['DNN face detector option',
         'Better recall in dense crowds (30 px faces). 5 MB model download.'],
    ],
    headers=['Feature', 'Notes'],
    col_widths=[5.5 * cm, 11.0 * cm],
))

story.append(Spacer(1, 18))
story.append(hr())
story.append(Paragraph(
    '<i>End of document.</i>',
    styles['Caption']))


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
doc = SimpleDocTemplate(
    OUT, pagesize=A4,
    leftMargin=2 * cm, rightMargin=2 * cm,
    topMargin=2 * cm, bottomMargin=1.8 * cm,
    title='FaceMark Attendance — Product Documentation',
    author='FaceMark',
)


# Two-page strategy:
# - Page 1 = cover (custom drawn by cover_page)
# - Page 2..N = content with chapter_header on every page
class _PdfDoc(SimpleDocTemplate):
    def handle_pageBegin(self):
        self._handle_pageBegin()


# Tiny blank flowable for the cover page (cover_page draws the visuals)
story = [Spacer(1, 1), PageBreak()] + story

doc.build(story,
          onFirstPage=cover_page,
          onLaterPages=chapter_header)

print('Wrote', OUT)
print('Size:', os.path.getsize(OUT), 'bytes')
