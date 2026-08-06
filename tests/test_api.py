"""API authentication, copilot intents, SCIM auth, SDK signing."""

import os
import time
import hmac
import hashlib
import json


def test_api_v1_health_public(client):
    r = client.get('/api/v1/health')
    assert r.status_code == 200
    assert r.get_json()['ok'] is True


def test_api_v1_requires_token(client):
    r = client.get('/api/v1/persons')
    assert r.status_code == 401


def test_api_v1_accepts_valid_token(client):
    import db
    raw, _ = db.create_api_key('pytest', 'read,write')
    r = client.get('/api/v1/persons',
                   headers={'Authorization': f'Bearer {raw}'})
    assert r.status_code == 200
    assert 'persons' in r.get_json()


def test_copilot_intent_classification(client):
    from enterprise import copilot
    res = copilot.answer('who was late this week?', actor='pytest')
    assert res['ok'] is True
    assert res['intent'] == 'late_count'

    res = copilot.answer('top 5 attenders this month', actor='pytest')
    assert res['ok'] is True
    assert res['intent'] == 'top_attenders'

    res = copilot.answer('tell me a joke', actor='pytest')
    assert res['ok'] is False  # unknown intent → friendly fallback


def test_copilot_blocks_unsafe_sql():
    from enterprise import copilot
    with __import__('pytest').raises(ValueError):
        copilot._safe_run('DROP TABLE persons', ())


def test_scim_rejects_no_token(client):
    r = client.get('/scim/v2/Users')
    assert r.status_code == 401


def test_scim_accepts_valid_token(client):
    import db
    raw, _ = db.add_scim_client('pytest-scim')
    r = client.get('/scim/v2/Users',
                   headers={'Authorization': f'Bearer {raw}'})
    assert r.status_code == 200
    j = r.get_json()
    assert 'Resources' in j


def test_sdk_rejects_bad_signature(client):
    import db
    pub, sec, _ = db.add_sdk_client('pytest-sdk', origins='')
    body = json.dumps({'person_id': '1', 'image': ''}).encode()
    r = client.post('/sdk/v1/auth', data=body,
                    headers={'X-FaceMark-Client': pub,
                             'X-FaceMark-Signature': 'WRONG',
                             'X-FaceMark-Ts': str(int(time.time())),
                             'Content-Type': 'application/json'})
    assert r.status_code == 401


def test_sdk_rejects_stale_timestamp(client):
    import db
    pub, sec, _ = db.add_sdk_client('pytest-stale', origins='')
    h = hashlib.sha256(sec.encode()).hexdigest()
    body = json.dumps({'person_id': '1', 'image': ''}).encode()
    stale = str(int(time.time()) - 600)
    sig = hmac.new(h.encode(), f'{stale}|'.encode() + body,
                   hashlib.sha256).hexdigest()
    r = client.post('/sdk/v1/auth', data=body,
                    headers={'X-FaceMark-Client': pub,
                             'X-FaceMark-Signature': sig,
                             'X-FaceMark-Ts': stale,
                             'Content-Type': 'application/json'})
    assert r.status_code == 401


def test_csrf_blocks_unauthenticated_form_post(client):
    """A POST without csrf_token to a UI endpoint must be 400."""
    os.environ.pop('FACEMARK_TEST_BYPASS_CSRF', None)
    r = client.post('/branches', data={'name': 'should-fail'})
    assert r.status_code == 400
