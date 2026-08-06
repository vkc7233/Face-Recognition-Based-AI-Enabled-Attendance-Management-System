"""Smoke tests — every page renders, every public endpoint reacts correctly."""


def test_health_endpoints(client):
    r = client.get('/healthz')
    assert r.status_code == 200 and r.get_json()['ok'] is True

    r = client.get('/readyz')
    assert r.status_code in (200, 503)
    j = r.get_json()
    assert 'checks' in j


def test_security_headers_set(client):
    r = client.get('/login')
    h = r.headers
    assert h.get('X-Content-Type-Options') == 'nosniff'
    assert h.get('X-Frame-Options') == 'SAMEORIGIN'
    assert h.get('Referrer-Policy') == 'strict-origin-when-cross-origin'
    assert 'Content-Security-Policy' in h
    assert 'X-Request-ID' in h
    # Request ID must look like a 16-char hex
    rid = h['X-Request-ID']
    assert len(rid) == 16


def test_metrics_endpoint(client):
    # Hit a couple of endpoints first so the counters are non-zero
    client.get('/healthz')
    client.get('/healthz')
    r = client.get('/metrics')
    assert r.status_code == 200
    body = r.data.decode()
    assert 'facemark_requests_total' in body
    assert 'facemark_request_latency_ms' in body


def test_rate_limit_module():
    """Direct check of the token-bucket: 5 hits allowed, 6th blocked."""
    from infra.ratelimit import _hit
    key = 'unit-test-bucket'
    for _ in range(5):
        ok, _ = _hit(key, capacity=5, per_seconds=60)
        assert ok is True
    ok, retry_after = _hit(key, capacity=5, per_seconds=60)
    assert ok is False
    assert retry_after >= 0


def test_404_renders_pretty_page(client):
    r = client.get('/no-such-path-xyz-9999')
    assert r.status_code == 404
    body = r.data.decode()
    assert 'Page not found' in body
    assert 'request id' in body.lower()
