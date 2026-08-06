"""
Knowledge base for the AI Workforce Copilot.

This is the answer source for every "how do I…", "what is…", "where do I…",
"why is…" type question — i.e. anyone who doesn't know the platform yet.
Each entry has:

  * `patterns` — regex / keyword cues that trigger the answer
  * `topic` — short label shown in the recent-questions table
  * `title` — bold title at the top of the answer
  * `steps` — ordered list rendered as a numbered guide
  * `links` — { label, href } items shown as buttons at the bottom

Add to this file freely — every new entry teaches the Copilot one more
thing.

Coverage targets ALL feature areas:

  Core      enrolment, kiosk, manual mark, history, ID cards, visitors
  Reports   payroll, defaulters, analytics, at-risk, parent pack
  Academic  subjects, sessions, timetable, holidays, defaulter rule
  Workforce branches, shifts, leaves, contractors, site muster, PPE
  Security  liveness, encryption, BYO-KMS, RBAC, audit, SIEM, GDPR
  Reach     PWA, portal, WhatsApp, SMS, RTSP, transport, mustering
  Platform  SSO, SCIM, multi-tenant, white-label, SDK
  Operate   docker, backup, settings, health, metrics

If you don't see an answer for a question your user asked — open a PR
adding a new entry below.
"""

from __future__ import annotations

import re
from typing import Optional


KB = [
    # ──────────────────────────────────────────────────────────────────
    # PLATFORM OVERVIEW — the answer to "what is this", "tell me everything"
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'overview',
        'patterns': [
            r'(complete|full|entire|all)\s+(knowledge|info|overview|tour|details|guide)',
            r'(everything|all features)\s+(about|of)?\s*(the )?(platform|product|system|app|facemark)',
            r'(tell|explain|describe)\s+(me\s+)?(about|everything)?\s*(the )?(platform|product|facemark)',
            r'(what|how)\s+(is|does)\s+(this|facemark|the platform|the product)',
            r'(platform|product)\s+(overview|tour|guide|introduction|intro)',
            r'(give|show)\s+(me\s+)?(a\s+)?(tour|overview|introduction)',
            r'introduc(e|tion)?\s+(me|the)?\s+(to|of)?\s+(the\s+)?(platform|product|app|system|facemark)',
            r'i.?m new( here)?',
            r'(new|first time)\s+(user|here)',
            r'how does (this|it|the platform|facemark) work',
            r'what (can|does) (this|it|facemark) do',
            r'documentation', r'\bdocs?\b', r'\bmanual\b', r'\bguide\b',
            r'(getting|how to get) started',
            r'^(help|hi|hello|hey)$',
        ],
        'title': 'Welcome to FaceMark — here\'s the complete picture',
        'steps': [
            '**Core**: FaceMark is an on-premise face-recognition attendance platform. It captures faces from a webcam or RTSP camera, matches them in real time, and records check-in / check-out with a late flag.',
            '**Privacy-first**: Everything runs on your servers; biometrics never leave the building. Templates can be AES-256 encrypted with your own KMS key.',
            '**Trust & anti-fraud**: Liveness (eye-blink + motion + texture), deepfake & virtual-camera detection, geofencing for mobile check-in, anti-passback, tailgating detection.',
            '**Reach**: Kiosk mode, big-screen display, mobile PWA, parent / staff portal, Slack + Teams check-in bot, GPS check-in, WhatsApp / SMS / email alerts.',
            '**Workforce**: Multi-branch, shifts, leaves, approval workflows, holidays, recurring timetable, payroll export, HRMS connectors (SAP SF, Workday, ADP, BambooHR, Zoho, Keka, greytHR, Zapier).',
            '**Reports & AI**: Daily digest emails, attendance heatmaps, at-risk attrition score, burnout signals, AI Copilot for plain-English questions (this is the Copilot you\'re using right now!).',
            '**Construction edition**: Contractors, daily-wage muster roll, PPE / helmet detection, site-gate geofence.',
            '**Enterprise IT**: SSO (Okta / Entra / Google), SCIM auto-provisioning, SIEM streaming (Splunk / Datadog / CEF / LEEF), BYO-KMS, SOC 2 + ISO 27001 evidence pack, multi-tenant control plane, white-label, face-auth SDK.',
            '**Operations**: Docker one-command deploy, encrypted off-site backup, /healthz + /readyz + Prometheus /metrics, structured JSON access log, role-based access (admin / hr / teacher / staff), full audit trail.',
            '**Languages**: English, Hindi, Marathi, Gujarati, Tamil, Kannada. **20 unique enterprise features** numbered N1-N20.',
        ],
        'links': [
            {'label': 'Enterprise Hub',     'href': '/enterprise/'},
            {'label': '20 features (N1-N20)', 'href': '/enterprise/'},
            {'label': 'Pricing tiers',      'href': '/enterprise/pricing'},
            {'label': 'Comparison vs Rippling/UKG', 'href': '/enterprise/comparison'},
            {'label': 'Proxy-Proof status', 'href': '/proxy-proof'},
            {'label': 'Documentation PDF',  'href': '/docs'},
        ],
    },

    # ──────────────────────────────────────────────────────────────────
    # Catalog: "what can you do" / "help" / "list features"
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'catalog',
        'patterns': [r'^help$', r'what can you (do|answer)',
                     r'list (your )?(features|capabilities|skills)',
                     r'\bcapabilities\b'],
        'title': 'Here\'s what the Copilot can answer or do',
        'steps': [
            'Attendance questions — ask "who was late this week?", "how many entries today?", "absent today", "top 5 attenders this month", "average check-in time last week", "overtime trend".',
            'Person-specific — ask "show Ravi\'s history", "is Sara at risk?".',
            'Reports & exports — Payroll, Parent-meeting pack, Per-subject defaulters.',
            'Setup help — ask "how do I register a user?", "how do I set up SSO?", "how do I enable liveness?" — I will give you step-by-step instructions.',
            'Enterprise features — ask "what is N5?" or "what does deepfake detection do?" — every N1-N20 feature has a guide.',
            'Or for a full overview: ask **"tell me about the platform"** or **"complete knowledge"**.',
        ],
        'links': [
            {'label': 'Platform overview', 'href': '/enterprise/'},
            {'label': 'Pricing', 'href': '/enterprise/pricing'},
        ],
    },

    # ──────────────────────────────────────────────────────────────────
    # "where is X" / navigation questions
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'navigation',
        'patterns': [
            r'where (is|are|do i find|can i find) (the )?(menu|nav|navigation|sidebar)',
            r'how (do|to|can) (i )?navigate',
            r'where (is|are) everything',
            r'(show me|where is) the (main )?(menu|sidebar)',
        ],
        'title': 'How to navigate FaceMark',
        'steps': [
            'Left sidebar — every section grouped: **Academics**, **Operate**, **Workforce**, **Reports**, **Enterprise**, **IT & Security**, **Admin**.',
            'Top of the sidebar: a **search box** — type a keyword like "leave" or "payroll" and only matching links remain (press `/` to focus it).',
            'Top bar shows the **current page title** + status pills (Online, Model trained, Liveness on) + your **role chip** with a dropdown to Settings / Audit / Sign out.',
            'A floating **💬 Copilot** button (bottom-right) is available on every page — click it to ask me anything from anywhere.',
        ],
        'links': [{'label': 'Dashboard', 'href': '/'}],
    },

    # ──────────────────────────────────────────────────────────────────
    # Core attendance — registering, marking, daily ops
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'how-to: register a user',
        'patterns': [r'(how|where) (do|to|can) (i )?(register|enrol|enroll|add) (a )?(user|person|employee|student|staff)',
                     r'register (a )?(new )?(user|person)',
                     r'add (a )?new (user|person|employee|student)',
                     r'(create|onboard) (a )?(user|person|employee)'],
        'title': 'Register a new user',
        'steps': [
            'Go to **Dashboard** (the home page).',
            'On the right side, find the **Register a user** panel.',
            'Fill in the full name and Roll No. / Employee ID.',
            'Pick a Class / Department from the dropdown (optional).',
            'Add email + guardian email + date of birth (all optional).',
            'Click **Start capture** — your webcam opens.',
            'Look into the camera. The system captures ~25 sharp face samples automatically (you\'ll see the count tick up).',
            'When capture finishes, the recogniser retrains automatically and the new person is live.',
        ],
        'links': [
            {'label': 'Go to Dashboard', 'href': '/'},
            {'label': 'List users', 'href': '/listusers'},
        ],
    },
    {
        'topic': 'how-to: mark attendance',
        'patterns': [r'(how|where) (do|to|can) (i )?(mark|take|record) attendance',
                     r'how (does )?(face )?(attendance|recognition) work',
                     r'how (do|to) (start|run|use) (live )?recognition'],
        'title': 'How attendance marking works',
        'steps': [
            'Open the Dashboard.',
            'In the **Live recognition** panel, click **Start**.',
            'The kiosk runs in **Crowd mode** — it processes every face in the frame at once.',
            'Each face is shown with its confidence number; once it gets 3 hits in 2.5 s, it\'s marked **CAPTURED**.',
            'A green check ✓ flashes on screen so the person knows they\'re done.',
            'Stop the live feed with the **Stop** button when finished.',
        ],
        'links': [
            {'label': 'Open Kiosk mode', 'href': '/kiosk'},
            {'label': 'Big-screen display', 'href': '/display'},
        ],
    },
    {
        'topic': 'how-to: manual check-in / out',
        'patterns': [r'manual (check.?in|check.?out|mark)',
                     r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(manually )?(mark|check.?in|check.?out)',
                     r'override attendance'],
        'title': 'Mark attendance manually (override)',
        'steps': [
            'Open **Users** in the sidebar.',
            'Find the person and click their name.',
            'On their profile, use the **Check in** or **Check out** button.',
            'The action is recorded in the audit log so it\'s traceable.',
        ],
        'links': [{'label': 'Users', 'href': '/listusers'}],
    },
    {
        'topic': 'how-to: check daily attendance',
        'patterns': [r'how (do|to) (check|see|view) (today.?s )?attendance',
                     r'where (is|do i find) (today.?s )?(attendance|register)',
                     r'who came (today|in)',
                     r'today.?s (attendance|register|report)'],
        'title': 'See today\'s attendance',
        'steps': [
            'Open the **Dashboard**.',
            'The KPI tiles at the top show: Registered users, Present today, Late today, Attendance rate.',
            'Below that, the **Today\'s attendance** table lists every check-in/out with status chips.',
            'For history: open **History** in the sidebar and pick a date.',
            'For printable: click the printable register link in History.',
        ],
        'links': [{'label': 'Dashboard', 'href': '/'},
                  {'label': 'History', 'href': '/history'}],
    },

    # ──────────────────────────────────────────────────────────────────
    # Trust / privacy / liveness
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'how-to: enable liveness',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(turn|enable|switch).*livenes',
                     r'liveness.*(on|off|enable|disable|toggle)',
                     r'how (do|to) stop photo attack',
                     r'(anti.?spoof|stop spoofing)'],
        'title': 'Enable liveness anti-spoofing',
        'steps': [
            'Open **Settings** in the sidebar.',
            'Find the **Liveness anti-spoofing** card.',
            'Toggle **Require eye-blink + motion + texture check before marking**.',
            'Click **Save settings**.',
            'From now on, the recogniser waits for a real blink + small head motion + non-screen texture before it marks anyone. A printed photo cannot pass.',
        ],
        'links': [{'label': 'Open Settings', 'href': '/settings'},
                  {'label': 'Proxy-Proof status', 'href': '/proxy-proof'}],
    },
    {
        'topic': 'what-is: liveness',
        'patterns': [r'what (is|does) liveness',
                     r'liveness anti.?spoof(ing)?',
                     r'how (does )?liveness work'],
        'title': 'What "liveness" means',
        'steps': [
            'Liveness is the check that proves the face in front of the camera belongs to a real human, not a photo, video, or mask.',
            'FaceMark runs three orthogonal checks in parallel:',
            '— Eye-blink: a real user blinks every 2-6 s; a printed photo never blinks.',
            '— Motion: the bounding box must drift naturally; a fixed photo doesn\'t move.',
            '— Texture: the FFT high-frequency band of a screen / glossy print is unnaturally flat.',
            'If liveness is on (default), all three must pass before attendance is recorded.',
        ],
    },
    {
        'topic': 'what-is: lbph',
        'patterns': [r'what (is|does) lbph', r'lbph.* (means|stands)',
                     r'local binary'],
        'title': 'LBPH — Local Binary Patterns Histograms',
        'steps': [
            'LBPH is the face-recognition algorithm FaceMark uses by default.',
            'It compares each pixel to its 8 neighbours and writes a binary pattern, then builds a histogram of those patterns over an 8×8 grid of the face.',
            'Compared to deep learning: ~85-92% accuracy vs 98-99%, but trains in seconds and runs at >30 fps on a Raspberry Pi.',
            'You can switch to deep embeddings under **Settings → Recogniser backend → embeddings**, then retrain.',
        ],
        'links': [{'label': 'Settings', 'href': '/settings'}],
    },
    {
        'topic': 'how-to: encrypt templates',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(encrypt|secure) (face|biometric|template)',
                     r'(turn on|enable|switch on) (template )?encryption',
                     r'encrypted templates'],
        'title': 'Encrypt biometric templates at rest',
        'steps': [
            'Open **Settings** in the sidebar.',
            'Find the **Liveness anti-spoofing** card; the encryption switch is right next to it.',
            'Toggle **Encrypt biometric templates at rest (AES-256)**.',
            'Save.',
            'Open **Privacy console** in the sidebar and click **Encrypt all existing templates** — this re-encrypts everything already on disk.',
            'For customer-managed keys: open **BYO-KMS** under Enterprise → add an AWS / GCP / Azure / Vault key.',
        ],
        'links': [{'label': 'Settings', 'href': '/settings'},
                  {'label': 'Privacy console', 'href': '/privacy'},
                  {'label': 'BYO-KMS', 'href': '/enterprise/kms'}],
    },

    # ──────────────────────────────────────────────────────────────────
    # Enterprise / Integration
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'how-to: configure SSO',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(set ?up|configure|enable) (sso|oidc|saml|okta|entra|azure ad|google)',
                     r'sso ?(setup|config(uration)?)',
                     r'(add|connect) (an? )?identity provider'],
        'title': 'Set up SSO (Okta / Entra ID / Google Workspace)',
        'steps': [
            'In your IdP, create an OIDC application. Copy the issuer URL, client ID and client secret.',
            'In FaceMark, open **Enterprise → SSO (N1)**.',
            'Click **Add provider**, pick the preset (Google / Okta / Entra) and paste the issuer + client ID + secret.',
            'Set the email-domain whitelist if you want to restrict who can sign in.',
            'Pick the default role for new users (admin / hr / teacher / staff).',
            'Click **Save provider**.',
            'In your IdP, set the redirect URI shown at the bottom of the SSO page.',
            'On the login page you\'ll now see a "Continue with …" button for that provider.',
        ],
        'links': [{'label': 'Open SSO admin', 'href': '/enterprise/sso'}],
    },
    {
        'topic': 'how-to: SCIM provisioning',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(set ?up|configure|enable) (scim|auto.?provision)',
                     r'auto.?provisioning',
                     r'(create|generate) scim token'],
        'title': 'Enable SCIM auto-provisioning',
        'steps': [
            'Open **Enterprise → SCIM (N2)**.',
            'Click **Generate token**, give it a name (e.g. "Okta production").',
            'Copy the token immediately — it\'s only shown once.',
            'In your IdP\'s SCIM connector, paste the FaceMark SCIM URL (shown at the top of the page) and the bearer token.',
            'When IT adds or removes someone from the IdP, FaceMark mirrors the change automatically.',
        ],
        'links': [{'label': 'Open SCIM admin', 'href': '/enterprise/scim'}],
    },
    {
        'topic': 'how-to: stream audit to SIEM',
        'patterns': [r'siem', r'splunk', r'datadog', r'audit.*stream',
                     r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(configure|set) audit'],
        'title': 'Stream audit log to your SIEM',
        'steps': [
            'Open **Enterprise → SIEM (N3)**.',
            'Click **Add sink**, fill in the destination URL (Splunk HEC, Datadog Logs Intake, etc.).',
            'Add the auth header (e.g. "Authorization: Splunk <token>").',
            'Pick the format: JSON (default), CEF (ArcSight), or LEEF (QRadar).',
            'Click **Drain now** to ship the pending events.',
            'After that, a background thread drains pending events every 15 s.',
        ],
        'links': [{'label': 'Open SIEM admin', 'href': '/enterprise/siem'}],
    },
    {
        'topic': 'how-to: configure connectors',
        'patterns': [r'(payroll|hrms) (connector|integration)',
                     r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(connect|integrate) (sap|workday|adp|bamboo|zoho|keka|greythr)',
                     r'zapier'],
        'title': 'Connect FaceMark to your HRMS / payroll',
        'steps': [
            'Open **Enterprise → HRMS connectors (N15)**.',
            'Click **Add connector**, pick the kind (SAP SF / Workday / ADP / BambooHR / Zoho People / Keka / greytHR / Zapier / Make).',
            'Paste the destination endpoint URL and the API key.',
            'Save.',
            'Click **Sync all now** to push the current month — verify the response in the table.',
            'A nightly scheduler will keep it pushing automatically.',
        ],
        'links': [{'label': 'Connectors', 'href': '/enterprise/connectors'}],
    },

    # ──────────────────────────────────────────────────────────────────
    # Workforce: shifts / leaves / payroll / branches
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'how-to: add a branch',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(add|create) (a )?(branch|site|location)',
                     r'add (a )?new (branch|site|office)',
                     r'(set ?up|configure) geofence'],
        'title': 'Add a branch / site (with geofence)',
        'steps': [
            'Open **Branches / Sites** in the sidebar.',
            'Fill in the name and address.',
            'For a simple round geofence: enter latitude, longitude, radius in metres.',
            'For a polygon: paste a JSON array of [lat,lng] pairs.',
            'Click **Save branch**.',
            'Mobile GPS check-ins will now be accepted only inside this fence.',
        ],
        'links': [{'label': 'Branches', 'href': '/branches'}],
    },
    {
        'topic': 'how-to: define a shift',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(add|create|define) (a )?shift',
                     r'shift (setup|window)',
                     r'(set|configure) (work )?hours'],
        'title': 'Define a shift',
        'steps': [
            'Open **Shifts** in the sidebar.',
            'Set name, start + end time, grace minutes.',
            'Set the days mask (Mon→Sun bits, e.g. 1111100 for Mon-Fri).',
            'Optionally restrict to a branch / department.',
            'Save.',
            'Late and overtime are now computed against THIS shift, not just the global work-start time.',
        ],
        'links': [{'label': 'Shifts', 'href': '/shifts'}],
    },
    {
        'topic': 'how-to: apply for leave',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(apply|request) (for )?leave',
                     r'leave (application|request)',
                     r'(submit|file) leave'],
        'title': 'Apply for leave (admin side)',
        'steps': [
            'Open **Leaves** in the sidebar.',
            'On the right, pick the person, leave type, from/to dates, reason.',
            'Submit.',
            'Approvers see it in the **Pending decisions** list and click Approve / Reject.',
            'Approved leave days no longer count as "absent" in payroll.',
        ],
        'links': [{'label': 'Leaves', 'href': '/leaves'}],
    },
    {
        'topic': 'how-to: run payroll',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(run|generate|export) payroll',
                     r'payroll (export|csv|excel)',
                     r'monthly hours'],
        'title': 'Generate the payroll run',
        'steps': [
            'Open **Payroll** in the sidebar.',
            'Pick the From and To dates.',
            'Click **Refresh** — the per-person table loads in one query (no per-person N+1).',
            'Use the **CSV** / **Excel** buttons in the hero to export.',
            'For real payroll integration, set up an HRMS connector under Enterprise → Connectors.',
        ],
        'links': [{'label': 'Payroll', 'href': '/payroll'},
                  {'label': 'Connectors', 'href': '/enterprise/connectors'}],
    },

    # ──────────────────────────────────────────────────────────────────
    # Site / Construction edition
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'how-to: enable site mode',
        'patterns': [r'(site|construction).*mode',
                     r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(turn|enable|switch) on (site|construction)',
                     r'(contractor|muster|ppe)'],
        'title': 'Turn on the Construction Site edition',
        'steps': [
            'Open **Settings**.',
            'Find the **Site mode (Construction Edition)** card.',
            'Toggle **Enable contractor + muster + PPE features**.',
            'Save.',
            'A new **Site** section appears in the sidebar with Contractors, Muster roll, and PPE incidents.',
            'Add contractors first, then add daily muster entries with hours + rate.',
        ],
        'links': [{'label': 'Settings', 'href': '/settings'},
                  {'label': 'Contractors', 'href': '/site/contractors'}],
    },

    # ──────────────────────────────────────────────────────────────────
    # GDPR / DPDP
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'how-to: record consent',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(record|capture) consent',
                     r'gdpr|dpdp',
                     r'biometric consent'],
        'title': 'Record DPDP / GDPR consent',
        'steps': [
            'Open **Privacy console** in the sidebar.',
            'In the **Consents on file** card, scroll to "Record consent".',
            'Enter the person ID, pick the purpose (biometric / notifications / payroll), pick granted/revoked.',
            'Paste the consent wording shown to the person (this is the legal proof).',
            'Save. An HMAC-signed proof signature is stored so it\'s tamper-evident.',
        ],
        'links': [{'label': 'Privacy console', 'href': '/privacy'}],
    },
    {
        'topic': 'how-to: erase a person',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(erase|delete) (a |all )?(person|user|data)',
                     r'right.?to.?(erasure|be forgotten)',
                     r'forget (me|a person)'],
        'title': 'Right-to-erasure (DPDP §11 / GDPR Art 17)',
        'steps': [
            'Open the person\'s profile (Users → click the name).',
            'In the right column, find **Reports & data**.',
            'Click **Right-to-erasure** — confirm the warning.',
            'Their face crops, attendance, leaves, consents, GPS marks, muster entries are deleted; the recogniser retrains automatically.',
            'For data portability instead of deletion, use **Full data export (ZIP)** — that gives them a portable JSON dump.',
        ],
        'links': [{'label': 'Privacy console', 'href': '/privacy'}],
    },

    # ──────────────────────────────────────────────────────────────────
    # Operate / ops
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'how-to: deploy with docker',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(deploy|install|run) (with )?docker',
                     r'docker.?compose'],
        'title': 'Deploy with Docker',
        'steps': [
            'From the project root, run `docker compose up -d`.',
            'Browse to http://localhost:8000 (note port 8000, not 5000).',
            'State persists in the named `data` volume — DB + face crops + visitor snapshots are kept across upgrades.',
            'For HTTPS, uncomment the nginx service in docker-compose.yml and add your cert.',
        ],
        'links': [],
    },
    {
        'topic': 'how-to: backup',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(back ?up|backup|snapshot)',
                     r'(disaster|recovery)'],
        'title': 'Set up encrypted off-site backup',
        'steps': [
            'Open **Backup** in the sidebar (Admin section).',
            'Pick at least one destination: local directory, PUT URL (S3/R2/etc.), or a shell command (rclone/rsync).',
            'Toggle **Run nightly at 02:30** if you want it automated.',
            'Click **Run a backup now** to test.',
            'The bundle is AES-256 encrypted with a key derived from FACEMARK_SECRET — only your secret can decrypt.',
        ],
        'links': [{'label': 'Backup', 'href': '/backup'}],
    },
    {
        'topic': 'how-to: check health',
        'patterns': [r'(how|where) (do|to)(?:\s+\w+){0,2}\s+(check|see|view) health',
                     r'(is it )?(working|healthy|alive)',
                     r'health.?check',
                     r'\b(uptime|status)\b'],
        'title': 'Check that FaceMark is healthy',
        'steps': [
            'GET /healthz — fast liveness probe, always 200 if the process is up.',
            'GET /readyz — deep readiness check: returns 200 + JSON when DB, recogniser, disk, and SIEM queue are all healthy; returns 503 otherwise.',
            'GET /metrics — Prometheus exposition with request counts, latency percentiles, registered persons, present today.',
            'For SOC analysts: every response carries an X-Request-ID; cross-reference it with /audit and your SIEM.',
        ],
        'links': [{'label': '/healthz', 'href': '/healthz'},
                  {'label': '/readyz', 'href': '/readyz'},
                  {'label': '/metrics', 'href': '/metrics'},
                  {'label': 'Audit log', 'href': '/audit'}],
    },

    # ──────────────────────────────────────────────────────────────────
    # Generic "what is N5 / N1 / N13" etc.
    # ──────────────────────────────────────────────────────────────────
    {
        'topic': 'what-is: N1 SSO',
        'patterns': [r'\bn1\b', r'what (is|does) sso',
                     r'sso (vs|or) password'],
        'title': 'N1 — SSO / SAML / OIDC',
        'steps': [
            'Lets users sign in with their corporate identity provider (Okta, Microsoft Entra ID, Google Workspace).',
            'Replaces the local username + password and removes the #1 IT-shortlisting blocker.',
            'Configure under Enterprise → SSO. Each provider takes about 5 minutes to set up.',
        ],
        'links': [{'label': 'Configure SSO', 'href': '/enterprise/sso'}],
    },
    {
        'topic': 'what-is: N5 deepfake',
        'patterns': [r'\bn5\b', r'deepfake', r'(virtual|inject(ion)?) camera',
                     r'face.?swap'],
        'title': 'N5 — Deepfake & injection-attack defense',
        'steps': [
            'Detects three families of 2026 spoofing vectors that the big HR suites ignore:',
            '— Virtual camera apps (OBS Virtual Camera, ManyCam, Snap Camera, DroidCam, etc.) by device-name match.',
            '— Replayed videos by frame-difference flatness + frozen-background hash.',
            '— Deepfake artefacts via a 6-feature classifier (edge density, skin smoothness, V-channel anomaly, JPEG ghost, …).',
            'Every detection is logged under Enterprise → Deepfake events with a snapshot.',
        ],
        'links': [{'label': 'Deepfake events', 'href': '/enterprise/spoof'}],
    },
    {
        'topic': 'what-is: N13 copilot',
        'patterns': [r'\bn13\b', r'what (is|does) (the )?copilot',
                     r'(ai|workforce) copilot'],
        'title': 'N13 — AI Workforce Copilot',
        'steps': [
            'Lets you ask attendance + setup questions in plain English instead of building a custom report.',
            'Uses a rule-based intent pipeline → safe parameterised SQL — never f-strings user text into queries.',
            'Mirrors what UKG and Rippling charge a premium for, at zero extra LLM cost.',
            'Try: "who was late this week?", "top 5 attenders this month", "show me overtime trend".',
        ],
    },
]


# ---------------------------------------------------------------------------
def _compile(pat: str) -> re.Pattern:
    return re.compile(pat, re.IGNORECASE)


# Light typo / synonym normalisation — runs BEFORE the regex match so a
# regular human spelling something approximately still hits the right entry.
_TYPO_FIX = {
    # common typos / shorthand
    'knowlwdge': 'knowledge', 'knowlege': 'knowledge', 'knwoledge': 'knowledge',
    'pltform': 'platform', 'platfrom': 'platform', 'platforn': 'platform',
    'attendence': 'attendance', 'attendence?': 'attendance',
    'employe': 'employee', 'emloyee': 'employee',
    'instructns': 'instructions', 'instrctions': 'instructions',
    'evrything': 'everything', 'eveything': 'everything',
    'liveniss': 'liveness', 'livness': 'liveness', 'livenss': 'liveness',
    'enroll': 'enrol', 'enrollment': 'enrolment',
    'registr': 'register', 'regstr': 'register', 'regiser': 'register',
    'creat': 'create', 'mke': 'make',
    'introduce': 'introduction', 'intro': 'introduction',
    # casual phrasings → friendly synonyms
    'thanks': 'help', 'thx': 'help',
    'wat': 'what', 'wht': 'what', 'whts': 'what is',
    'whtas': 'what is', 'whats': 'what is',
    'hw': 'how', 'pls': 'please', 'plz': 'please',
    'config': 'configure', 'setup': 'set up',
    'doc': 'docs', 'documentation?': 'documentation',
}


def _normalise(q: str) -> str:
    q = (q or '').lower().strip()
    # Strip punctuation that isn't a word boundary (keep apostrophes)
    q = re.sub(r"[^a-z0-9'\s%]+", ' ', q)
    # Collapse repeated spaces
    q = re.sub(r'\s+', ' ', q)
    # Word-level typo fix
    tokens = []
    for tok in q.split(' '):
        tokens.append(_TYPO_FIX.get(tok, tok))
    return ' '.join(tokens).strip()


# Keyword index for the "weak match" fallback — used when no regex hits
# but the question still leans toward a known topic.
_KEYWORDS_PER_ENTRY: list[tuple[dict, set[str]]] = []


def _build_keyword_index() -> None:
    """Extract noun-like keywords from each entry's title + steps, so we can
    still hint at a topic when the regex pipeline misses."""
    global _KEYWORDS_PER_ENTRY
    stop = {
        'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'is',
        'with', 'for', 'into', 'by', 'from', 'this', 'that', 'these',
        'your', 'you', 'i', 'we', 'they', 'it', 'will', 'be', 'as', 'so',
        'when', 'where', 'how', 'what', 'why', 'do', 'does', 'did', 'open',
        'click', 'select', 'go', 'find', 'see', 'use', 'add', 'new',
    }
    out = []
    for e in KB:
        words: set[str] = set()
        for s in [e.get('title', '')] + list(e.get('steps', [])):
            for tok in re.findall(r"[a-z][a-z0-9-]{2,}", s.lower()):
                if tok in stop:
                    continue
                words.add(tok)
        out.append((e, words))
    _KEYWORDS_PER_ENTRY = out


_COMPILED = [
    {**e, '_pats': [_compile(p) for p in e['patterns']]}
    for e in KB
]
_build_keyword_index()


def match(question: str) -> Optional[dict]:
    """Return a KB entry whose patterns match, scored by how many match.

    Three layers of matching, in order:
      1) Direct regex hit (after typo normalisation).
      2) Strong keyword overlap (≥ 2 distinct content tokens shared with
         an entry's keyword index).
      3) Special-case: very short or greeting-style questions ("help",
         "hi", "what is this") fall through to the platform overview.
    """
    if not question:
        return None
    q_norm = _normalise(question)

    # Layer 1 — regex
    best, best_score = None, 0
    for entry in _COMPILED:
        score = sum(1 for p in entry['_pats'] if p.search(q_norm))
        if score > best_score:
            best_score, best = score, entry
    if best:
        return best

    # Layer 2 — keyword overlap
    q_tokens = set(re.findall(r"[a-z][a-z0-9-]{2,}", q_norm))
    if q_tokens:
        best, best_overlap = None, 0
        for entry, words in _KEYWORDS_PER_ENTRY:
            overlap = len(q_tokens & words)
            if overlap > best_overlap:
                best_overlap, best = overlap, entry
        if best_overlap >= 2:
            return best

    # Layer 3 — vague / greeting → overview
    if len(q_norm) < 20 or any(g in q_norm for g in ('hi', 'hello', 'hey')):
        for entry in KB:
            if entry['topic'] == 'overview':
                return entry
    return None


def topics() -> list[str]:
    return [e['topic'] for e in KB]
