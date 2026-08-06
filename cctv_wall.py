"""Live grid of all working NVR channels.

Routes:
    /cctv             - grid HTML
    /cctv_feed/<ch>   - MJPEG stream for one channel (sub-stream by default)

Configuration is via env vars or settings.json-style fallbacks at the top.
The credentials default to the values verified by rtsp_probe_all.py, but you
should normally override them with environment variables in production:

    NVR_HOST=192.168.1.100
    NVR_USER=admin
    NVR_PASS=...
    NVR_CHANNELS=1,2,4,7,8
"""
from __future__ import annotations

import os
import time
from urllib.parse import quote

import cv2
import numpy as np
from flask import Blueprint, Response, render_template, abort, session, redirect, url_for, request, jsonify

NVR_HOST = os.environ.get("NVR_HOST", "192.168.1.100")
NVR_PORT = int(os.environ.get("NVR_PORT", "554"))
NVR_USER = os.environ.get("NVR_USER", "admin")
NVR_PASS = os.environ.get("NVR_PASS", "Sangath@sj1981")
CHANNELS = [int(c) for c in os.environ.get(
    "NVR_CHANNELS", "1,2,4,7,8").split(",") if c.strip()]

# Sub-streams (lower res) keep the grid responsive on commodity hardware.
# Switch to "{ch}01" if your NVR's sub-stream is disabled or you want full-res.
PATH_TEMPLATE = os.environ.get(
    "NVR_PATH_TEMPLATE",
    "/Streaming/Channels/{ch}02",
)

cctv_bp = Blueprint("cctv", __name__)


@cctv_bp.before_request
def _require_login():
    if not session.get("admin"):
        if request.path.startswith("/cctv_feed"):
            return jsonify({"ok": False, "msg": "auth required"}), 401
        return redirect(url_for("login", next=request.path))


def rtsp_url(channel: int) -> str:
    u = quote(NVR_USER, safe="")
    p = quote(NVR_PASS, safe="")
    return f"rtsp://{u}:{p}@{NVR_HOST}:{NVR_PORT}{PATH_TEMPLATE.format(ch=channel)}"


def _placeholder_jpeg(text: str) -> bytes:
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(img, text, (40, 180),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 220), 2)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes() if ok else b""


def _mjpeg(channel: int):
    url = rtsp_url(channel)
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        jpeg = _placeholder_jpeg(f"ch {channel}: cannot open")
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
        return
    try:
        last_ok = time.time()
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                if time.time() - last_ok > 5:  # 5s of nothing = give up
                    break
                time.sleep(0.05)
                continue
            last_ok = time.time()
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ok:
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                   + buf.tobytes() + b"\r\n")
    finally:
        cap.release()


@cctv_bp.route("/cctv")
def cctv_wall():
    return render_template("cctv_wall.html", channels=CHANNELS, host=NVR_HOST)


@cctv_bp.route("/cctv_feed/<int:ch>")
def cctv_feed(ch: int):
    if ch not in CHANNELS:
        abort(404)
    return Response(_mjpeg(ch),
                    mimetype="multipart/x-mixed-replace; boundary=frame")
