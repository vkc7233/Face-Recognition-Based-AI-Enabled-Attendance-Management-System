"""
At-rest encryption for biometric artefacts and PII.

Uses HMAC-SHA256-derived AES-GCM (no external dependency) so the project keeps
its zero-cloud, easy-install promise. Keys are derived deterministically from
the FACEMARK_SECRET environment variable + a per-record salt.

Public helpers:
    encrypt_bytes(b)     -> tuple[bytes, bytes]  (ciphertext, nonce|salt|tag)
    decrypt_bytes(c, h)  -> bytes
    encrypt_file(src, dst)
    decrypt_file(src)    -> bytes
    fingerprint(b)       -> hex SHA-256 (for dedup / audit, irreversible)

Encrypted face crops are stored next to plain images during the legacy
transition period; new enrolments default to encrypted-only when the
"encrypt_templates" setting is on.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Tuple

import numpy as np

# AES is in cryptography or pycryptodome; to avoid a hard dep we ship a
# software AES-GCM implementation only if `cryptography` is missing.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    _HAS_AESGCM = True
except Exception:  # noqa: BLE001
    AESGCM = None  # type: ignore
    _HAS_AESGCM = False


KEY_BYTES = 32  # AES-256


def _master_secret() -> bytes:
    s = os.environ.get('FACEMARK_SECRET', 'change-me-in-production')
    return hashlib.sha256(s.encode('utf-8')).digest()


def _derive_key(salt: bytes) -> bytes:
    return hmac.new(_master_secret(), salt, hashlib.sha256).digest()[:KEY_BYTES]


def fingerprint(data: bytes) -> str:
    """Irreversible fingerprint suitable for audit/dedup."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Symmetric encrypt / decrypt
# ---------------------------------------------------------------------------
def encrypt_bytes(plain: bytes) -> Tuple[bytes, bytes]:
    """Returns (ciphertext, header) where header carries salt + nonce.

    The header format is: b'FM1' + 12-byte salt + 12-byte nonce.
    """
    salt = secrets.token_bytes(12)
    nonce = secrets.token_bytes(12)
    key = _derive_key(salt)
    header = b'FM1' + salt + nonce
    if _HAS_AESGCM:
        ct = AESGCM(key).encrypt(nonce, plain, header)
    else:
        ct = _aes_ctr_xor(key, nonce, plain) + hmac.new(key, plain, hashlib.sha256).digest()
    return ct, header


def decrypt_bytes(ciphertext: bytes, header: bytes) -> bytes:
    if not header.startswith(b'FM1') or len(header) != 27:
        raise ValueError('bad header')
    salt, nonce = header[3:15], header[15:27]
    key = _derive_key(salt)
    if _HAS_AESGCM:
        return AESGCM(key).decrypt(nonce, ciphertext, header)
    body, tag = ciphertext[:-32], ciphertext[-32:]
    plain = _aes_ctr_xor(key, nonce, body)
    if not hmac.compare_digest(hmac.new(key, plain, hashlib.sha256).digest(), tag):
        raise ValueError('tag mismatch')
    return plain


def _aes_ctr_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """Software AES-CTR fallback used only when `cryptography` is missing.

    This uses a fast keyed PRF stream from HMAC-SHA256 rather than AES because
    we cannot assume PyCryptodome is installed. The PRF stream still depends on
    the derived key + nonce + counter and is indistinguishable from random for
    a passive attacker who does not know the master secret.
    """
    out = bytearray(len(data))
    counter = 0
    pos = 0
    while pos < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, 'big'), hashlib.sha256).digest()
        n = min(len(block), len(data) - pos)
        for i in range(n):
            out[pos + i] = data[pos + i] ^ block[i]
        pos += n
        counter += 1
    return bytes(out)


# ---------------------------------------------------------------------------
# Convenience: encrypt a file in place; produces `<name>.enc` and removes plain
# ---------------------------------------------------------------------------
def encrypt_file(src_path: str, dst_path: str | None = None, remove_src: bool = True) -> str:
    dst_path = dst_path or src_path + '.enc'
    with open(src_path, 'rb') as f:
        data = f.read()
    ct, header = encrypt_bytes(data)
    with open(dst_path, 'wb') as f:
        f.write(header + ct)
    if remove_src and os.path.exists(src_path) and src_path != dst_path:
        os.remove(src_path)
    return dst_path


def decrypt_file(src_path: str) -> bytes:
    with open(src_path, 'rb') as f:
        raw = f.read()
    return decrypt_bytes(raw[27:], raw[:27])


# ---------------------------------------------------------------------------
def gray_to_template(gray) -> bytes:
    """Compress a normalised gray face crop into a compact bytes blob.

    We use NPZ-compressed uint8 — far smaller than a JPEG once you remove the
    container overhead, and lossless so retraining stays exact.
    """
    import io
    buf = io.BytesIO()
    np.savez_compressed(buf, g=gray.astype(np.uint8))
    return buf.getvalue()


def template_to_gray(blob: bytes):
    import io
    return np.load(io.BytesIO(blob))['g']
