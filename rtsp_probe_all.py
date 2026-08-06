"""Headless: confirm which of the 10 channels actually return a frame."""
import os, time
from urllib.parse import quote

NVR_IP, USERNAME, PASSWORD = "192.168.1.100", "admin", "Sangath@sj1981"
RTSP_PORT, TOTAL = 554, 10

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|stimeout;3000000|max_delay;500000"
)
import cv2  # noqa: E402

u = quote(USERNAME, safe="")
p = quote(PASSWORD, safe="")

def url_main(ch):  # main stream — full resolution
    return f"rtsp://{u}:{p}@{NVR_IP}:{RTSP_PORT}/Streaming/Channels/{ch}01"

def url_sub(ch):   # sub stream — lower res, lighter on CPU
    return f"rtsp://{u}:{p}@{NVR_IP}:{RTSP_PORT}/Streaming/Channels/{ch}02"

def probe(ch, url):
    t0 = time.time()
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        return f"ch {ch:2d}  [FAIL] open  ({time.time()-t0:.1f}s)"
    w = h = 0
    for _ in range(8):
        ret, frame = cap.read()
        if ret and frame is not None and frame.size > 0:
            h, w = frame.shape[:2]
            break
    cap.release()
    if w:
        return f"ch {ch:2d}  [OK]  {w}x{h}  ({time.time()-t0:.1f}s)"
    return f"ch {ch:2d}  [?]   opened, no frame  ({time.time()-t0:.1f}s)"

print(f"Probing channels 1..{TOTAL} on {NVR_IP} (main stream)…\n")
for ch in range(1, TOTAL + 1):
    print(probe(ch, url_main(ch)), flush=True)
