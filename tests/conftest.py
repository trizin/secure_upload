"""Test fixtures. Environment is configured before the app is imported,
because config reads it at import time."""

import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="supload-test-")
os.environ["SUPLOAD_DATA"] = _TMP
os.environ["SUPLOAD_ENFORCE_HOSTS"] = "0"
os.environ["SUPLOAD_SCHEME"] = "http"
os.environ["SUPLOAD_APP_HOST"] = "testserver"
os.environ["SUPLOAD_FILE_HOST"] = "files.testserver"
os.environ["SUPLOAD_RATE_UPLOADS"] = "10000"
os.environ["SUPLOAD_RATE_BYTES"] = str(10 * 1024**3)

import pytest
from fastapi.testclient import TestClient

from app import config, db, main


@pytest.fixture(scope="session", autouse=True)
def _cleanup():
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture()
def client():
    config.ensure_dirs()
    db.init()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def upload(client):
    def _upload(name, data, content_type="application/octet-stream"):
        return client.post(
            "/api/upload", files={"file": (name, data, content_type)}
        )
    return _upload
