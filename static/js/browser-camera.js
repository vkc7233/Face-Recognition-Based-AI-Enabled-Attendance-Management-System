/*
 * FaceMark browser camera.
 *
 * The server-side pipeline (cv2.VideoCapture) only sees a camera attached to
 * the machine running Python. That works for an on-premise box and not at all
 * for a hosted deployment, where the container has no camera device. This
 * module captures from the operator's own webcam with getUserMedia and POSTs
 * JPEG frames to /api/camera/frame, which runs the identical pipeline.
 *
 * Usage:
 *   const cam = FaceMarkCamera.create({
 *     video:   <video> element used as the capture source,
 *     mode:    'register' | 'recognise',
 *     name/uid: required for 'register',
 *     wantImage: true to receive the annotated frame back,
 *     onResult: fn(json)  — called per processed frame
 *     onError:  fn(message, kind)
 *   });
 *   await cam.start();  cam.stop();
 */
window.FaceMarkCamera = (function () {
  'use strict';

  // getUserMedia is only exposed in a secure context. https:// and
  // http://localhost qualify; http:// to a LAN IP does not, and the API is
  // simply absent rather than failing — worth saying so explicitly, because
  // "navigator.mediaDevices is undefined" is not an actionable error.
  function unsupportedReason() {
    if (!window.isSecureContext) {
      return 'Camera needs a secure connection. Open this page over https://, ' +
             'or via http://localhost during development.';
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return 'This browser does not support camera capture (getUserMedia).';
    }
    return null;
  }

  function describeError(err) {
    switch (err && err.name) {
      case 'NotAllowedError':
      case 'SecurityError':
        return 'Camera permission was blocked. Allow camera access for this ' +
               'site in your browser settings, then reload.';
      case 'NotFoundError':
      case 'OverconstrainedError':
        return 'No camera was found on this device.';
      case 'NotReadableError':
      case 'TrackStartError':
        return 'The camera is already in use by another app (Zoom, Teams, ' +
               'Meet). Close it and reload.';
      default:
        return 'Could not start the camera: ' + ((err && err.message) || err);
    }
  }

  function create(opts) {
    const video = opts.video;
    const mode = opts.mode;
    const fps = opts.fps || 4;
    const width = opts.width || 640;
    const onResult = opts.onResult || function () {};
    const onError = opts.onError || function () {};

    let stream = null;
    let timer = null;
    let inFlight = false;      // one frame on the wire at a time
    let stopped = false;
    const canvas = document.createElement('canvas');

    function toBlob() {
      return new Promise(function (resolve) {
        const vw = video.videoWidth, vh = video.videoHeight;
        if (!vw || !vh) { resolve(null); return; }
        // Downscale to `width` before upload. A 1280px frame is ~4x the bytes
        // and gains nothing: the detector runs on a 200px face crop.
        const scale = Math.min(1, width / vw);
        canvas.width = Math.round(vw * scale);
        canvas.height = Math.round(vh * scale);
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(resolve, 'image/jpeg', 0.7);
      });
    }

    async function tick() {
      // Skip rather than queue — if the server or link is slow, piling up
      // frames would make the preview drift further behind with every tick.
      if (inFlight || stopped) return;
      inFlight = true;
      try {
        const blob = await toBlob();
        if (!blob || stopped) return;
        const fd = new FormData();
        fd.append('frame', blob, 'f.jpg');
        fd.append('mode', mode);
        if (opts.wantImage) fd.append('want_image', '1');
        if (mode === 'register') {
          fd.append('name', opts.name);
          fd.append('uid', opts.uid);
        }
        // base.html patches window.fetch to attach X-CSRF-Token. Standalone
        // pages (kiosk.html) do not extend base.html, so they pass the token
        // in explicitly.
        const init = { method: 'POST', body: fd };
        if (opts.csrf) init.headers = { 'X-CSRF-Token': opts.csrf };
        const r = await fetch('/api/camera/frame', init);
        const j = await r.json().catch(function () { return null; });
        if (stopped) return;
        if (!r.ok || !j || !j.ok) {
          onError((j && (j.msg || j.error)) || ('Server error ' + r.status),
                  (j && j.error) || 'server');
          return;
        }
        onResult(j);
      } catch (e) {
        if (!stopped) onError('Network error while sending the frame.', 'network');
      } finally {
        inFlight = false;
      }
    }

    async function start() {
      const why = unsupportedReason();
      if (why) { onError(why, 'unsupported'); return false; }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false
        });
      } catch (e) {
        onError(describeError(e), (e && e.name) || 'getusermedia');
        return false;
      }
      video.srcObject = stream;
      video.setAttribute('playsinline', '');   // iOS Safari: don't go fullscreen
      video.muted = true;
      try { await video.play(); } catch (e) { /* autoplay policies; harmless */ }
      stopped = false;
      timer = setInterval(tick, Math.round(1000 / fps));
      return true;
    }

    function stop() {
      stopped = true;
      if (timer) { clearInterval(timer); timer = null; }
      if (stream) {
        stream.getTracks().forEach(function (t) { t.stop(); });
        stream = null;
      }
      // Releases the OS camera handle so the indicator light goes out.
      if (video) video.srcObject = null;
    }

    return { start: start, stop: stop };
  }

  return { create: create, unsupportedReason: unsupportedReason };
})();
