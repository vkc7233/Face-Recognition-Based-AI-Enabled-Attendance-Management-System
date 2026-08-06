# FaceMark - on-premise face-recognition attendance.
#
# This image bundles OpenCV (with the contrib LBPH module), Flask and
# everything needed to run FaceMark as a turnkey container. It mounts a
# host volume at /data for the SQLite DB, enrolment crops and visitor
# snapshots so updates don't lose attendance history.
#
# Build:  docker build -t facemark .
# Run:    docker run -d --name facemark -p 8000:8000 \
#             -e FACEMARK_SECRET=change-me \
#             -v facemark-data:/data --restart unless-stopped facemark
#
# To use an RTSP IP camera, set the `camera_url` setting via Settings.
# The container has no webcam access by default; pass --device /dev/video0
# on Linux hosts if you want the kiosk webcam.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FACEMARK_DATA=/data

# OpenCV runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Persisted state — DB, enrolled faces, profile thumbs, visitors
RUN mkdir -p /data && \
    ln -sfn /data/facemark.db   /app/facemark.db && \
    ln -sfn /data/static_faces  /app/static/faces && \
    ln -sfn /data/static_profiles /app/static/profiles && \
    ln -sfn /data/static_visitors /app/static/visitors && \
    ln -sfn /data/static_ppe    /app/static/ppe

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "--threads", "4", "app:app"]
