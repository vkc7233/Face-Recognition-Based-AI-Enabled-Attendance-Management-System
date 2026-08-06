"""Probe an NVR/IP-camera to find the right RTSP URL format.

Headless: tries known URL patterns on channel 1 only, prints which one(s)
work, and exits. Uses FFMPEG with a short open-timeout so a wrong URL
fails fast instead of hanging.
"""
import os, sys, time
from urllib.parse import quote

# ── Edit these three lines if needed ─────────────────────────────────
NVR_IP   = "192.168.1.100"
USERNAME = "admin"
PASSWORD = "Sangath@sj1981"
RTSP_PORT = 554
# ─────────────────────────────────────────────────────────────────────

# OpenCV uses FFmpeg for RTSP. These env vars must be set BEFORE importing cv2.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|stimeout;3000000|max_delay;500000"
)
import cv2  # noqa: E402

u = quote(USERNAME, safe="")
p = quote(PASSWORD, safe="")
auth = f"{u}:{p}"

CANDIDATES = {
    "Dahua main (ch1)":     f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/cam/realmonitor?channel=1&subtype=0",
    "Dahua sub (ch1)":      f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/cam/realmonitor?channel=1&subtype=1",
    "Hikvision main (101)": f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/Streaming/Channels/101",
    "Hikvision sub (102)":  f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/Streaming/Channels/102",
    "Hik ISAPI ch01 main":  f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/ISAPI/Streaming/channels/101",
    "TP-Link/CP+ stream1":  f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/stream1",
    "CP Plus / generic ch1 main": f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/ch01/main/av_stream",
    "Generic ch1 sub":      f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/ch01/sub/av_stream",
    "Uniview live ch1":     f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/unicast/c1/s0/live",
    "ONVIF profile S (h264)": f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/onvif1",
    "ONVIF profile T":      f"rtsp://{auth}@{NVR_IP}:{RTSP_PORT}/Streaming/Unicast/channels/101",
}

def probe(label: str, url: str) -> str:
    safe_url = url.replace(p, "***").replace(u, "***")
    t0 = time.time()
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    opened = cap.isOpened()
    frame_ok = False
    w = h = 0
    if opened:
        # Read up to 5 frames — first one is often empty on RTSP.
        for _ in range(5):
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                frame_ok = True
                h, w = frame.shape[:2]
                break
    cap.release()
    dt = time.time() - t0
    if frame_ok:
        return f"  [OK ] {label:30s} {w}x{h}  ({dt:.1f}s)  {safe_url}"
    if opened:
        return f"  [?  ] {label:30s} opened but no frame  ({dt:.1f}s)"
    return f"  [FAIL] {label:30s} did not open  ({dt:.1f}s)"

if __name__ == "__main__":
    print(f"Probing {NVR_IP} as {USERNAME}…  (each attempt has a ~5s timeout)")
    print(f"OpenCV {cv2.__version__}\n")
    winners = []
    for label, url in CANDIDATES.items():
        line = probe(label, url)
        print(line, flush=True)
        if line.lstrip().startswith("[OK ]"):
            winners.append((label, url))
    print()
    if winners:
        print("=== WORKING URL(S) ===")
        for label, url in winners:
            print(f"  {label}\n    {url}")
        print("\nUse the first working URL as the template; change `channel=1`")
        print("(or `/101`, `/ch01/`) to 2..10 for the other channels.")
        sys.exit(0)
    print("No URL worked. Likely causes:")
    print("  - Wrong username/password (RTSP often uses a SEPARATE password from the web UI)")
    print("  - NVR has RTSP disabled — enable it in the NVR web UI > Network > RTSP")
    print("  - Different port (try 10554, 8554)")
    print("  - Brand-specific path not in this list — check the NVR's ONVIF/RTSP info page")
    sys.exit(1)
