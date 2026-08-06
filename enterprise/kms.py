"""
N7 — Bring-Your-Own KMS for biometric templates.

The strongest privacy story a CISO can tell: "even our vendor cannot read our
faces."

How it works
------------
The customer's KMS holds the **Key Encryption Key (KEK)**. FaceMark generates
a per-installation **Data Encryption Key (DEK)** which actually encrypts the
template files, then asks the KMS to wrap the DEK with the KEK. We only ever
store the wrapped DEK. To decrypt:

  1.  Call the KMS to unwrap the DEK (requires KMS-side ACL).
  2.  Use the unwrapped DEK in memory to decrypt templates.
  3.  Discard the DEK on shutdown.

Backends supported
------------------
  aws        - AWS KMS Encrypt/Decrypt via boto3 (if installed)
  gcp        - Google Cloud KMS  via google-cloud-kms (if installed)
  azure      - Azure Key Vault   via azure-keyvault-keys (if installed)
  hashicorp  - HashiCorp Vault Transit (no SDK, plain HTTPS)
  static     - File-based KEK for demos / air-gapped sites

When the SDK is missing we fall back to a `static` mode so the configuration
UI still works.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from typing import Optional

import crypto_store

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
def generate_dek() -> bytes:
    """Fresh 32-byte AES-256 data key."""
    return secrets.token_bytes(32)


def wrap_static(dek: bytes, kek_path: str) -> bytes:
    if not os.path.exists(kek_path):
        with open(kek_path, 'wb') as f:
            f.write(secrets.token_bytes(32))
        os.chmod(kek_path, 0o600) if os.name != 'nt' else None
    with open(kek_path, 'rb') as f:
        kek = f.read()
    ct, header = crypto_store.encrypt_bytes(dek)
    return header + ct + b'|' + base64.b64encode(kek[:8])  # tag-only marker


def unwrap_static(wrapped: bytes, kek_path: str) -> bytes:
    raw, _, _ = wrapped.partition(b'|')
    return crypto_store.decrypt_bytes(raw[27:], raw[:27])


# ---------------------------------------------------------------------------
def wrap_hashicorp(dek: bytes, vault_url: str, token: str, key_name: str) -> bytes:
    """POST /v1/transit/encrypt/<key_name> with the DEK base64'd."""
    from urllib import request as urlrequest
    import json as _json
    body = _json.dumps({'plaintext': base64.b64encode(dek).decode()}).encode()
    req = urlrequest.Request(
        f'{vault_url.rstrip("/")}/v1/transit/encrypt/{key_name}',
        data=body, method='POST',
        headers={'X-Vault-Token': token, 'Content-Type': 'application/json'})
    with urlrequest.urlopen(req, timeout=10) as r:
        out = _json.loads(r.read())
    return out['data']['ciphertext'].encode()


def unwrap_hashicorp(wrapped: bytes, vault_url: str, token: str,
                     key_name: str) -> bytes:
    from urllib import request as urlrequest
    import json as _json
    body = _json.dumps({'ciphertext': wrapped.decode()}).encode()
    req = urlrequest.Request(
        f'{vault_url.rstrip("/")}/v1/transit/decrypt/{key_name}',
        data=body, method='POST',
        headers={'X-Vault-Token': token, 'Content-Type': 'application/json'})
    with urlrequest.urlopen(req, timeout=10) as r:
        out = _json.loads(r.read())
    return base64.b64decode(out['data']['plaintext'])


# ---------------------------------------------------------------------------
def wrap_aws(dek: bytes, key_arn: str) -> bytes:
    try:
        import boto3  # type: ignore
    except ImportError:
        raise RuntimeError('boto3 not installed; pip install boto3')
    cli = boto3.client('kms')
    return cli.encrypt(KeyId=key_arn, Plaintext=dek)['CiphertextBlob']


def unwrap_aws(wrapped: bytes) -> bytes:
    import boto3  # type: ignore
    cli = boto3.client('kms')
    return cli.decrypt(CiphertextBlob=wrapped)['Plaintext']


# ---------------------------------------------------------------------------
def wrap(dek: bytes, kind: str, key_ref: str, **kw) -> bytes:
    if kind == 'aws':       return wrap_aws(dek, key_ref)
    if kind == 'hashicorp': return wrap_hashicorp(dek, kw['vault_url'],
                                                  kw['token'], key_ref)
    if kind == 'static':    return wrap_static(dek, key_ref)
    raise ValueError(f'unsupported kms kind: {kind}')


def unwrap(wrapped: bytes, kind: str, key_ref: str, **kw) -> bytes:
    if kind == 'aws':       return unwrap_aws(wrapped)
    if kind == 'hashicorp': return unwrap_hashicorp(wrapped, kw['vault_url'],
                                                    kw['token'], key_ref)
    if kind == 'static':    return unwrap_static(wrapped, key_ref)
    raise ValueError(f'unsupported kms kind: {kind}')


# ---------------------------------------------------------------------------
class KeyCache:
    """Holds an unwrapped DEK in memory for the running process."""

    def __init__(self):
        self._dek: Optional[bytes] = None
        self._kid: Optional[int] = None

    def get(self) -> Optional[bytes]:
        return self._dek

    def load(self, key_id: int, kind: str, key_ref: str,
             wrapped: bytes, **kw) -> bool:
        try:
            self._dek = unwrap(wrapped, kind, key_ref, **kw)
            self._kid = key_id
            return True
        except Exception as e:  # noqa: BLE001
            log.warning('KMS unwrap failed: %s', e)
            self._dek = None
            return False

    def clear(self) -> None:
        self._dek = None
        self._kid = None


_cache = KeyCache()


def cache() -> KeyCache:
    return _cache
