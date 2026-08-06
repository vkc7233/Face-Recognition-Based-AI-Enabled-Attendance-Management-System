"""
N1 — SSO via OIDC (Okta, Microsoft Entra ID, Google Workspace).

Why this exists
---------------
Corporate IT will not shortlist a tool whose only login is a local username +
password. The OIDC flow here lets the customer point their identity provider
at FaceMark and have employees sign in with their corporate account.

Flow
----
1.  /sso/<provider_id>/login — builds the IdP authorize URL and redirects.
2.  IdP returns to /sso/<provider_id>/callback with `code` + `state`.
3.  We exchange the code for tokens, fetch /userinfo, validate the email
    domain whitelist, and create/refresh an admin_users row.
4.  Audit trail (category='auth') and `streamed=0` so the SIEM tail (N3)
    picks it up.

The OIDC details (issuer, client_id, client_secret, auth_url, token_url,
userinfo_url) are stored per-provider in the sso_providers table. The
Settings UI can paste them from any IdP's "OIDC application" page.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from typing import Optional
from urllib import parse, request as urlrequest

log = logging.getLogger(__name__)


# Provider presets — operators can pick a kind and only fill in the unique fields.
PRESETS = {
    'google': {
        'issuer':       'https://accounts.google.com',
        'auth_url':     'https://accounts.google.com/o/oauth2/v2/auth',
        'token_url':    'https://oauth2.googleapis.com/token',
        'userinfo_url': 'https://openidconnect.googleapis.com/v1/userinfo',
    },
    'okta': {
        # operator supplies issuer (e.g. https://acme.okta.com/oauth2/default)
        'auth_url':     '{issuer}/v1/authorize',
        'token_url':    '{issuer}/v1/token',
        'userinfo_url': '{issuer}/v1/userinfo',
    },
    'entra': {
        # operator supplies issuer (e.g. https://login.microsoftonline.com/<tenant>/v2.0)
        'auth_url':     '{issuer}/oauth2/v2.0/authorize',
        'token_url':    '{issuer}/oauth2/v2.0/token',
        'userinfo_url': 'https://graph.microsoft.com/oidc/userinfo',
    },
}

REQUIRED_SCOPES = 'openid email profile'


def expand_provider(provider: dict) -> dict:
    """Apply preset URLs if the operator picked a `kind` but didn't override them."""
    p = dict(provider)
    preset = PRESETS.get(p.get('kind', ''), {})
    issuer = (p.get('issuer') or '').rstrip('/')
    for k, v in preset.items():
        cur = p.get(k)
        if not cur and v:
            p[k] = v.replace('{issuer}', issuer) if issuer else v
    return p


# ---------------------------------------------------------------------------
# PKCE helpers — required by Okta/Entra public clients, recommended everywhere.
# ---------------------------------------------------------------------------
def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode('ascii')


def make_pkce() -> tuple[str, str]:
    """Returns (verifier, challenge). Verifier is stored in the session; only the
    challenge goes on the wire."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# ---------------------------------------------------------------------------
def build_authorize_url(provider: dict, redirect_uri: str,
                        state: str, code_challenge: str) -> str:
    p = expand_provider(provider)
    if not p.get('auth_url') or not p.get('client_id'):
        raise ValueError('provider missing auth_url or client_id')
    q = {
        'response_type':       'code',
        'client_id':           p['client_id'],
        'redirect_uri':        redirect_uri,
        'scope':               REQUIRED_SCOPES,
        'state':               state,
        'code_challenge':      code_challenge,
        'code_challenge_method': 'S256',
    }
    return p['auth_url'] + '?' + parse.urlencode(q)


def exchange_code(provider: dict, code: str, redirect_uri: str,
                  code_verifier: str) -> dict:
    p = expand_provider(provider)
    data = {
        'grant_type':    'authorization_code',
        'code':          code,
        'redirect_uri':  redirect_uri,
        'client_id':     p['client_id'],
        'code_verifier': code_verifier,
    }
    if p.get('client_secret'):
        data['client_secret'] = p['client_secret']
    req = urlrequest.Request(
        p['token_url'], data=parse.urlencode(data).encode(), method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Accept': 'application/json'})
    with urlrequest.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def fetch_userinfo(provider: dict, access_token: str) -> dict:
    p = expand_provider(provider)
    req = urlrequest.Request(
        p['userinfo_url'],
        headers={'Authorization': f'Bearer {access_token}',
                 'Accept': 'application/json'})
    with urlrequest.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def claim_email(info: dict) -> Optional[str]:
    return info.get('email') or info.get('preferred_username') or info.get('upn')


def claim_subject(info: dict) -> Optional[str]:
    return info.get('sub') or info.get('oid') or info.get('uid')


def claim_name(info: dict) -> Optional[str]:
    return info.get('name') or info.get('given_name') or claim_email(info)


def domain_allowed(provider: dict, email: Optional[str]) -> bool:
    """If `domain` is set on the provider, the email must end in it."""
    if not provider.get('domain'):
        return True
    if not email:
        return False
    return email.lower().endswith(provider['domain'].lower())


# Common placeholder values that mean "not configured yet"
_PLACEHOLDER_VALUES = {
    'abc', 'xxx', 'todo', 'placeholder', 'test', 'demo',
    'your-client-id', 'your_client_id', 'replace-me', 'changeme',
}


def is_usable(provider: dict) -> bool:
    """A provider is *usable* on the login page only if it has all the
    minimum config it needs to actually authenticate. We pre-validate so
    we don't show broken "Continue with …" buttons that immediately error
    at the IdP.
    """
    if not provider:
        return False
    if not provider.get('enabled'):
        return False
    p = expand_provider(provider)
    client_id = (p.get('client_id') or '').strip()
    auth_url  = (p.get('auth_url') or '').strip()
    token_url = (p.get('token_url') or '').strip()
    if not client_id or len(client_id) < 12:
        return False
    if client_id.lower() in _PLACEHOLDER_VALUES:
        return False
    if not auth_url or not auth_url.startswith(('http://', 'https://')):
        return False
    if not token_url or not token_url.startswith(('http://', 'https://')):
        return False
    # Custom OIDC kind needs an explicit issuer
    if p.get('kind') in ('oidc', 'okta', 'entra'):
        if not (p.get('issuer') or '').strip():
            return False
    return True


def list_usable(providers: list[dict]) -> list[dict]:
    return [p for p in providers if is_usable(p)]
