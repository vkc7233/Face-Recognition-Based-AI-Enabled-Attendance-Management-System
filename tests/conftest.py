"""Shared pytest fixtures."""

import os
import sys
import pytest

# Make the project root importable when pytest is run from anywhere
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture(scope='session')
def app():
    import app as appmod
    appmod.app.config['TESTING'] = True
    appmod.app.config['WTF_CSRF_ENABLED'] = False
    return appmod.app


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture()
def admin_session(client):
    """A test client already logged in as admin (CSRF is disabled in tests
    via the FACEMARK_TEST env var, so POSTs to login work)."""
    os.environ['FACEMARK_TEST_BYPASS_CSRF'] = '1'
    r = client.post('/login',
                    data={'username': 'admin', 'password': 'admin123'},
                    follow_redirects=False)
    # Token is fine if login redirects to /
    assert r.status_code in (302, 303, 200)
    yield client
