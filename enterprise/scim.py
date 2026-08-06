"""
N2 — SCIM 2.0 auto user provisioning.

Implements the subset of SCIM v2 that real IdPs (Okta, Azure AD, OneLogin,
JumpCloud) actually use for provisioning:

  GET  /scim/v2/Users
  POST /scim/v2/Users
  GET  /scim/v2/Users/<id>
  PATCH /scim/v2/Users/<id>
  DELETE /scim/v2/Users/<id>
  GET  /scim/v2/ServiceProviderConfig
  GET  /scim/v2/ResourceTypes
  GET  /scim/v2/Schemas

Auth is a Bearer token issued by FaceMark to the IdP (stored hashed).
"""

from __future__ import annotations

from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request

import db


scim_bp = Blueprint('scim_v2', __name__, url_prefix='/scim/v2')


# ---------------------------------------------------------------------------
def _auth():
    auth = request.headers.get('Authorization', '')
    raw = auth.split(' ', 1)[1].strip() if auth.startswith('Bearer ') else ''
    return db.verify_scim_token(raw)


def require_scim(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if not _auth():
            return jsonify({
                'schemas': ['urn:ietf:params:scim:api:messages:2.0:Error'],
                'detail': 'unauthorized', 'status': '401'}), 401
        return fn(*a, **kw)
    return wrap


def _person_to_scim(p: dict) -> dict:
    return {
        'schemas': ['urn:ietf:params:scim:schemas:core:2.0:User'],
        'id':       str(p['person_id']),
        'externalId': p.get('scim_external_id'),
        'userName': p.get('email') or p['person_id'],
        'name': {
            'formatted': p.get('name'),
            'givenName': (p.get('name') or '').split(' ')[0],
        },
        'displayName': p.get('name'),
        'emails': [{'value': p['email'], 'primary': True}] if p.get('email') else [],
        'active': True,
        'meta': {
            'resourceType': 'User',
            'created': p.get('created_at'),
            'location': f"/scim/v2/Users/{p['person_id']}",
        },
    }


# ---------------------------------------------------------------------------
@scim_bp.route('/ServiceProviderConfig')
def spc():
    return jsonify({
        'schemas': ['urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig'],
        'patch':    {'supported': True},
        'bulk':     {'supported': False},
        'filter':   {'supported': True, 'maxResults': 200},
        'changePassword': {'supported': False},
        'sort':     {'supported': False},
        'etag':     {'supported': False},
        'authenticationSchemes': [
            {'name': 'OAuth Bearer Token', 'type': 'oauthbearertoken',
             'description': 'FaceMark SCIM bearer'}],
    })


@scim_bp.route('/ResourceTypes')
def resource_types():
    return jsonify({
        'schemas': ['urn:ietf:params:scim:api:messages:2.0:ListResponse'],
        'totalResults': 1,
        'Resources': [{
            'id': 'User', 'name': 'User',
            'endpoint': '/Users',
            'description': 'FaceMark enrolled person',
            'schema': 'urn:ietf:params:scim:schemas:core:2.0:User',
        }],
    })


@scim_bp.route('/Schemas')
def schemas():
    return jsonify({
        'schemas': ['urn:ietf:params:scim:api:messages:2.0:ListResponse'],
        'totalResults': 1,
        'Resources': [
            {'id': 'urn:ietf:params:scim:schemas:core:2.0:User',
             'name': 'User'}
        ],
    })


@scim_bp.route('/Users')
@require_scim
def users_list():
    start = int(request.args.get('startIndex', '1'))
    count = int(request.args.get('count', '50'))
    persons = db.list_persons()
    sliced = persons[start - 1: start - 1 + count]
    return jsonify({
        'schemas': ['urn:ietf:params:scim:api:messages:2.0:ListResponse'],
        'totalResults': len(persons),
        'startIndex': start,
        'itemsPerPage': len(sliced),
        'Resources': [_person_to_scim(dict(p)) for p in sliced],
    })


@scim_bp.route('/Users', methods=['POST'])
@require_scim
def users_create():
    j = request.get_json(force=True, silent=True) or {}
    user_name = j.get('userName') or ''
    name = (j.get('name') or {}).get('formatted') or j.get('displayName') or user_name
    email = ''
    for e in j.get('emails', []):
        if e.get('primary'):
            email = e.get('value', '')
            break
    if not email and j.get('emails'):
        email = j['emails'][0].get('value', '')
    external_id = j.get('externalId') or user_name
    person_id = (j.get('id') or external_id).replace('@', '_at_')
    db.upsert_person(person_id, name or user_name, email=email or None)
    # Tag with the SCIM external id so future updates idempotently match
    from db import tx
    with tx() as c:
        c.execute('UPDATE persons SET scim_external_id = ? WHERE person_id = ?',
                  (external_id, person_id))
    person = db.get_person(person_id)
    return jsonify(_person_to_scim(dict(person))), 201


@scim_bp.route('/Users/<pid>')
@require_scim
def users_get(pid):
    person = db.get_person(pid)
    if not person:
        return jsonify({'schemas': ['urn:ietf:params:scim:api:messages:2.0:Error'],
                        'detail': 'not found', 'status': '404'}), 404
    return jsonify(_person_to_scim(dict(person)))


@scim_bp.route('/Users/<pid>', methods=['PATCH', 'PUT'])
@require_scim
def users_patch(pid):
    person = db.get_person(pid)
    if not person:
        return jsonify({'detail': 'not found', 'status': '404'}), 404
    j = request.get_json(force=True, silent=True) or {}
    ops = j.get('Operations') or [{'op': 'replace', 'value': j}]
    updates = {}
    for op in ops:
        v = op.get('value') or {}
        if isinstance(v, dict):
            if 'displayName' in v: updates['name'] = v['displayName']
            if 'name' in v and isinstance(v['name'], dict):
                updates['name'] = v['name'].get('formatted', updates.get('name'))
            if 'emails' in v and v['emails']:
                updates['email'] = v['emails'][0].get('value')
            if 'active' in v and v['active'] is False:
                db.delete_person(pid)
                return '', 204
    if updates:
        db.update_person(pid, **updates)
    return jsonify(_person_to_scim(dict(db.get_person(pid))))


@scim_bp.route('/Users/<pid>', methods=['DELETE'])
@require_scim
def users_delete(pid):
    db.delete_person(pid)
    return '', 204
