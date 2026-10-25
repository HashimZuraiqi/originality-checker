"""Test configuration: isolated database/storage, no network, fast models.

Environment variables must be set before the app's settings are first read.
"""

import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="oc-tests-")
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{_TMP}/test.db",
        "STORAGE_DIR": f"{_TMP}/files",
        "EMBEDDED_WORKER": "false",
        "EMBEDDING_PROVIDER": "lexical",
        "ANTHROPIC_API_KEY": "",
        "BRAVE_API_KEY": "",
        "GOOGLE_CSE_KEY": "",
        "LANGUAGETOOL_URL": "",
        "CONTACT_EMAIL": "tests@example.org",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import create_all, init_engine  # noqa: E402
from app.security import rate_limiter  # noqa: E402
from app.services.discovery import engine as discovery_engine  # noqa: E402
from app.services.discovery import http as discovery_http  # noqa: E402

from . import fakeweb  # noqa: E402

init_engine()
create_all()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    """No real network, no rate-limit carry-over, no provider throttling."""
    rate_limiter.reset()
    monkeypatch.setattr(discovery_http, "HOST_RATES", {})
    monkeypatch.setattr(discovery_http, "DEFAULT_RATE", 10_000.0)
    monkeypatch.setattr(discovery_http.limiter, "_next", {})
    monkeypatch.setattr(discovery_http, "_check_public_host", fakeweb.check_public_host)
    monkeypatch.setattr(discovery_http.time, "sleep", lambda s: None)
    fakeweb.reset()
    monkeypatch.setattr(discovery_engine, "http_client_factory", fakeweb.client)
    from app.db import session_scope
    from app.models import FetchCache, SearchCache

    with session_scope() as db:
        db.query(SearchCache).delete()
        db.query(FetchCache).delete()
    yield


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


_counter = {"n": 0}


def register(client: TestClient, name: str = "Test User") -> dict:
    _counter["n"] += 1
    email = f"user{_counter['n']}@example.org"
    r = client.post("/api/auth/register", json={"name": name, "email": email, "password": "CorrectHorse9Battery"})
    assert r.status_code == 201, r.text
    data = r.json()
    client.headers.update({"X-CSRF-Token": data["csrf_token"]})
    data["email"] = email
    return data


@pytest.fixture
def user(client):
    return register(client)
