# FaceMark — Face-Recognition Attendance

On-premise, no per-user fee. Crowd-mode LBPH + eye-blink liveness, kiosk and TV
displays, visitor log, ID cards, per-subject defaulter rule, audit log, branches,
shifts, leaves, recurring timetable, holiday calendar, payroll export, REST API
and webhooks, WhatsApp / SMS / email alerts, DPDP/GDPR consent + right-to-erasure,
encrypted templates at rest, GPS check-in with mock-location detection, multi-
language UI (English, Hindi, Marathi, Gujarati, Tamil, Kannada), RTSP / IP camera
support, role-based admin access, predictive at-risk analytics, and a
Construction Site Edition (contractors, daily-wage muster, PPE/helmet detection).

## Quick start

### Bare-metal

```
pip install -r requirements.txt
python app.py
```

Open (`https://face-recognition-based-ai-enabled.onrender.com/`). First login is `admin / admin123` — change it
immediately in Settings.

### Docker (one command)

```
docker compose up -d
```

Then visit <`https://face-recognition-based-ai-enabled.onrender.com/`>. State persists in the `data` volume.

## Configuration

Every feature is toggled in **Settings**:

- **Liveness anti-spoofing** — on by default; requires eye-blink + motion +
  texture before marking. Stops printed-photo and screen attacks.
- **Encrypted templates** — turn on to AES-256 encrypt face crops at rest. New
  enrolments are encrypted automatically; bulk-encrypt existing crops from
  Privacy console.
- **Camera URL** — leave blank for the local webcam; set
  `rtsp://user:pass@cam.local/Streaming/Channels/101` for an IP camera.
- **Notifications** — SMTP + SMS gateway URL + WhatsApp Cloud API. The system
  sends attendance updates, daily absence alerts and weekly at-risk digests.
- **Retention** — auto-purges visitor snapshots, notification log, audit log
  and GPS marks on a policy clock (days set per category).
- **Site mode** — turns on contractors, daily muster roll and PPE/helmet
  detection for construction deployments.

## REST API

Every endpoint is under `/api/v1/`. Create an API key in `/api-keys` and pass it
as `Authorization: Bearer fm_…`. Webhooks deliver signed JSON to your URL on
attendance events (`check_in`, `check_out`, `absent`, `visitor`).

## Modules

| Module           | Purpose                                                    |
|------------------|------------------------------------------------------------|
| `liveness.py`    | Eye-blink + motion + texture anti-spoofing                 |
| `crypto_store.py`| AES-GCM encryption + irreversible fingerprints             |
| `geo.py`         | Haversine, geofence polygons + fake-GPS detection          |
| `notify.py`      | Email / SMS / WhatsApp with queue + retries                |
| `ppe.py`         | Helmet + vest detector (site mode)                         |
| `payroll.py`     | Hours / overtime / wages computation                       |
| `restapi.py`     | `/api/v1/` Flask blueprint + webhook delivery              |
| `scheduler.py`   | Nightly purge, daily digest, weekly at-risk                |
| `i18n.py`        | English + 5 Indian languages                               |

## Construction Site Edition

When `site_mode_enabled` is on, FaceMark exposes contractors, a per-day muster
roll keyed by worker × contractor × site, automatic wage calculation, and a
gate-level PPE check that blocks attendance if a helmet or vest is missing.

## Documentation

The product PDF and the Feature Expansion analysis live in `docs/`.
