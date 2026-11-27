import pytest

from rnsd_webui.app import make_app
from rnsd_webui.auth import Credentials, Sessions
from rnsd_webui.backend import FakeBackend

PASSWORD = "correct horse battery"


@pytest.fixture()
def creds(tmp_path):
    c = Credentials(tmp_path / "data")
    c.set_password(PASSWORD)
    return c


@pytest.fixture()
def backend():
    return FakeBackend()


@pytest.fixture()
def sessions():
    return Sessions()


@pytest.fixture()
async def client(aiohttp_client, backend, creds, sessions):
    return await aiohttp_client(make_app(backend, creds, "test", demo=True, sessions=sessions))


@pytest.fixture()
async def authed(client):
    r = await client.post("/api/login", json={"password": PASSWORD})
    assert r.status == 200
    body = await r.json()
    client.csrf = body["csrf"]
    return client
