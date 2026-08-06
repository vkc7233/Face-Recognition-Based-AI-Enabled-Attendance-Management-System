"""
Optional encrypted off-site backup.

Bundles the SQLite DB, face crops, profile thumbs and visitor snapshots
into a single tarball, encrypts it with `crypto_store.encrypt_bytes()`
(AES-256-GCM with a key derived from FACEMARK_SECRET) and either:

  * writes it to a local backup directory (`backup_local_dir` setting), or
  * uploads it via HTTP PUT to a presigned URL (`backup_put_url` setting),
    works with S3-, R2-, Backblaze- or any other "PUT to URL" target, or
  * runs a user-supplied shell command (`backup_command` setting) with the
    path to the encrypted file as an argument (eg. rclone, rsync).

Nothing is ever sent in the clear — the master FACEMARK_SECRET is the only
thing that can decrypt the archive.

The scheduler calls `run_backup_once()` daily at 02:30 when
`backup_enabled` = '1'.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tarfile
from datetime import datetime
from urllib import request as urlrequest

import crypto_store
import db

log = logging.getLogger(__name__)

BACKUP_DIRS = ['static/faces', 'static/profiles', 'static/visitors',
               'static/ppe', 'static/models']
BACKUP_FILES = ['facemark.db']


def _build_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for f in BACKUP_FILES:
            if os.path.exists(f):
                tar.add(f, arcname=f)
        for d in BACKUP_DIRS:
            if os.path.isdir(d):
                tar.add(d, arcname=d)
    return buf.getvalue()


def run_backup_once() -> dict:
    """Produce + dispatch one backup, log to backup_log."""
    archive = _build_archive()
    ct, header = crypto_store.encrypt_bytes(archive)
    encrypted = header + ct
    name = f'facemark-{datetime.now().strftime("%Y%m%d-%H%M%S")}.tgz.enc'

    dest = ''
    status = 'skipped'
    detail = 'no-destination'

    local = (db.get_setting('backup_local_dir') or '').strip()
    if local:
        try:
            os.makedirs(local, exist_ok=True)
            out_path = os.path.join(local, name)
            with open(out_path, 'wb') as f:
                f.write(encrypted)
            dest, status, detail = out_path, 'ok', f'{len(encrypted)}b'
        except Exception as e:  # noqa: BLE001
            dest, status, detail = local, 'error', str(e)

    put_url = (db.get_setting('backup_put_url') or '').strip()
    if put_url:
        try:
            url = put_url.rstrip('/') + '/' + name
            req = urlrequest.Request(url, data=encrypted, method='PUT',
                                     headers={'Content-Type': 'application/octet-stream'})
            with urlrequest.urlopen(req, timeout=60) as r:
                dest = url
                status = 'ok' if 200 <= r.status < 300 else 'error'
                detail = f'http-{r.status}'
        except Exception as e:  # noqa: BLE001
            dest, status, detail = put_url, 'error', str(e)

    cmd = (db.get_setting('backup_command') or '').strip()
    if cmd:
        try:
            tmp = os.path.join('/tmp' if os.name != 'nt' else os.environ.get('TEMP', '.'),
                               name)
            with open(tmp, 'wb') as f:
                f.write(encrypted)
            proc = subprocess.run(cmd + ' ' + tmp, shell=True, check=False,
                                  capture_output=True, text=True, timeout=120)
            os.remove(tmp)
            dest = cmd
            status = 'ok' if proc.returncode == 0 else 'error'
            detail = (proc.stderr or proc.stdout or '')[:300]
        except Exception as e:  # noqa: BLE001
            dest, status, detail = cmd, 'error', str(e)

    db.log_backup(dest or 'none', len(encrypted), status, detail)
    return {'dest': dest, 'status': status, 'bytes': len(encrypted), 'detail': detail}


def restore_from_file(encrypted_path: str, target_dir: str = '.') -> dict:
    """Decrypt + extract a backup archive to `target_dir`. Used in DR drills."""
    with open(encrypted_path, 'rb') as f:
        raw = f.read()
    header, ct = raw[:27], raw[27:]
    plain = crypto_store.decrypt_bytes(ct, header)
    with tarfile.open(fileobj=io.BytesIO(plain), mode='r:gz') as tar:
        tar.extractall(target_dir)
    return {'restored_from': encrypted_path, 'into': target_dir}
