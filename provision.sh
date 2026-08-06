#!/usr/bin/env bash
# FaceMark — Turnkey Edge Appliance provisioning.
#
# Run this on a fresh Debian / Ubuntu / Raspberry Pi OS box that will sit at
# the gate. It installs system deps, sets up a Python venv, creates a systemd
# unit that auto-starts FaceMark on boot, and (optionally) puts nginx in
# front for HTTPS via Let's Encrypt.
#
# Usage:
#   sudo bash provision.sh [--domain attend.example.com] [--no-nginx]
#
# After it finishes, browse to http://<box-ip>:5000 (or your domain) and
# sign in with admin / admin123.

set -euo pipefail

DOMAIN=""
SETUP_NGINX=1
INSTALL_DIR="/opt/facemark"
SERVICE_USER="facemark"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --no-nginx) SETUP_NGINX=0; shift ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo bash provision.sh)" >&2
  exit 1
fi

echo "== FaceMark appliance provisioning =="

# ----------------------------------------------------------------------------
# 1) System packages
# ----------------------------------------------------------------------------
apt-get update -y
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
  v4l-utils sqlite3 git curl ca-certificates

# ----------------------------------------------------------------------------
# 2) Service user
# ----------------------------------------------------------------------------
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
usermod -aG video "$SERVICE_USER" || true

# ----------------------------------------------------------------------------
# 3) Install FaceMark (assumes you've copied the source to $INSTALL_DIR or
#    cloned a git repo there)
# ----------------------------------------------------------------------------
if [[ ! -d "$INSTALL_DIR" ]]; then
  echo "Copy the FaceMark source to $INSTALL_DIR first, then re-run." >&2
  exit 1
fi
cd "$INSTALL_DIR"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

# ----------------------------------------------------------------------------
# 4) Systemd unit
# ----------------------------------------------------------------------------
SECRET="$(head -c 24 /dev/urandom | base64 | tr -d '+/=')"
cat >/etc/systemd/system/facemark.service <<EOF
[Unit]
Description=FaceMark face-recognition attendance
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=FACEMARK_SECRET=$SECRET
ExecStart=$INSTALL_DIR/.venv/bin/python app.py
Restart=on-failure
RestartSec=5
LimitNOFILE=8192

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable facemark.service
systemctl restart facemark.service

# ----------------------------------------------------------------------------
# 5) Nginx + Let's Encrypt (optional)
# ----------------------------------------------------------------------------
if [[ "$SETUP_NGINX" == "1" && -n "$DOMAIN" ]]; then
  apt-get install -y nginx certbot python3-certbot-nginx
  cat >/etc/nginx/sites-available/facemark <<NGX
server {
  listen 80;
  server_name $DOMAIN;
  client_max_body_size 25M;
  location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_read_timeout 90s;
    proxy_buffering off;            # for the MJPEG video feed
  }
}
NGX
  ln -sfn /etc/nginx/sites-available/facemark /etc/nginx/sites-enabled/facemark
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  certbot --nginx -d "$DOMAIN" --redirect --agree-tos --register-unsafely-without-email -n || \
    echo "Let's Encrypt skipped (DNS may not resolve yet). Run certbot manually later."
fi

# ----------------------------------------------------------------------------
# 6) Done
# ----------------------------------------------------------------------------
IP=$(hostname -I | awk '{print $1}')
echo
echo "== FaceMark is running =="
echo "  Local:   http://${IP}:5000"
[[ -n "$DOMAIN" ]] && echo "  Public:  https://${DOMAIN}"
echo "  Secret:  $SECRET   (already set in the systemd unit)"
echo "  Logs:    journalctl -u facemark -f"
echo
echo "First login is admin / admin123 — change it under Settings."
