import asyncio
import json

from tests.conftest import PASSWORD


async def test_api_needs_a_session(client):
    for path in ("/api/status", "/api/paths", "/api/self", "/api/announces"):
        assert (await client.get(path)).status == 401
    r = await client.get("/api/session")
    assert (await r.json())["authenticated"] is False


async def test_wrong_password_and_throttle(client):
    for _ in range(5):
        assert (await client.post("/api/login", json={"password": "nope"})).status == 401
    r = await client.post("/api/login", json={"password": PASSWORD})
    assert r.status == 429                                       # even the right one waits out the window


async def test_login_sets_a_strict_httponly_cookie_and_logout_ends_it(client):
    r = await client.post("/api/login", json={"password": PASSWORD})
    assert r.status == 200
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert (await client.get("/api/status")).status == 200
    csrf = (await r.json())["csrf"]
    assert (await client.post("/api/logout", json={}, headers={"X-CSRF-Token": csrf})).status == 200
    assert (await client.get("/api/status")).status == 401


async def test_posts_need_the_csrf_token(authed, backend):
    assert (await authed.post("/api/announce", json={"app_data": "x"})).status == 403
    assert (await authed.post("/api/announce", json={"app_data": "x"}, headers={"X-CSRF-Token": "wrong"})).status == 403
    assert backend.announced == []
    r = await authed.post("/api/announce", json={"app_data": "hello"}, headers={"X-CSRF-Token": authed.csrf})
    assert r.status == 200 and backend.announced == ["hello"]


async def test_status_paths_and_self_have_the_expected_shapes(authed):
    s = await (await authed.get("/api/status")).json()
    assert s["transport"]["id"] and len(s["interfaces"]) == 2 and s["interfaces"][0]["status"] is True
    p = await (await authed.get("/api/paths")).json()
    assert {"hash", "via", "hops", "interface", "age", "expires_in"} <= set(p[0])
    me = await (await authed.get("/api/self")).json()
    assert len(me["destination"]) == 32


async def test_path_request_validates_the_hash(authed, backend):
    h = {"X-CSRF-Token": authed.csrf}
    for bad in ("", "xyz", "00" * 15, "zz" * 16, None, 5):
        r = await authed.post("/api/path-request", json={"hash": bad}, headers=h)
        assert r.status == 400, bad
    r = await authed.post("/api/path-request", json={"hash": "AB" * 16}, headers=h)
    assert r.status == 200 and backend.requested == ["ab" * 16]


async def test_announce_text_limits(authed):
    h = {"X-CSRF-Token": authed.csrf}
    assert (await authed.post("/api/announce", json={"app_data": "é" * 200}, headers=h)).status == 400   # 400 bytes
    assert (await authed.post("/api/announce", json={"app_data": 5}, headers=h)).status == 400
    assert (await authed.post("/api/announce", data="not json", headers=h)).status == 200               # empty body = default text


async def test_security_headers_and_no_caching_of_api(authed):
    r = await authed.get("/api/status")
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in r.headers["Content-Security-Policy"] and r.headers["X-Content-Type-Options"] == "nosniff"
    page = await authed.get("/")
    assert page.status == 200 and "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert (await authed.get("/static/app.js")).status == 200 and (await authed.get("/static/style.css")).status == 200


async def test_announce_buffer_and_live_stream(authed, backend):
    backend.record_announce({"time": 1.0, "destination": "aa" * 16, "identity": None, "hops": 2, "app_data": {"text": "first", "hex": "00"}})
    buffered = await (await authed.get("/api/announces")).json()
    assert buffered[0]["destination"] == "aa" * 16

    resp = await authed.get("/api/announces/stream")
    assert resp.status == 200 and resp.headers["Content-Type"].startswith("text/event-stream")
    await asyncio.sleep(0.1)
    backend.record_announce({"time": 2.0, "destination": "bb" * 16, "identity": None, "hops": 1, "app_data": {"text": None, "hex": "ff"}})
    got = b""
    while b"bb" * 16 not in got:
        got += await asyncio.wait_for(resp.content.readany(), timeout=3)
    event = [l for l in got.decode().splitlines() if l.startswith("data: ")][-1][6:]
    assert json.loads(event)["destination"] == "bb" * 16
    resp.close()


async def test_the_http_redirect_sends_browsers_to_https(aiohttp_client):
    from rnsd_webui.app import make_redirect_app
    c = await aiohttp_client(make_redirect_app(443))
    r = await c.get("/some/page?x=1", headers={"Host": "bobcat04.example.com"}, allow_redirects=False)
    assert r.status == 308 and r.headers["Location"] == "https://bobcat04.example.com/some/page?x=1"
    r = await c.get("/", headers={"Host": "node.local:80"}, allow_redirects=False)
    assert r.headers["Location"] == "https://node.local/"
    c2 = await aiohttp_client(make_redirect_app(8443))
    r = await c2.get("/", headers={"Host": "node.local"}, allow_redirects=False)
    assert r.headers["Location"] == "https://node.local:8443/"


async def test_the_http_redirect_refuses_odd_host_headers(aiohttp_client):
    from rnsd_webui.app import make_redirect_app
    c = await aiohttp_client(make_redirect_app(443))
    for bad in ("evil.com/@x", "x;y", "-bad-.com", "a..b"):
        r = await c.get("/", headers={"Host": bad}, allow_redirects=False)
        assert r.status == 400, bad


async def test_a_busy_redirect_port_does_not_stop_the_server(aiohttp_client, backend, creds, sessions, unused_tcp_port):
    import socket
    from rnsd_webui.app import make_app
    busy = socket.socket(); busy.bind(("127.0.0.1", unused_tcp_port)); busy.listen(1)
    try:
        c = await aiohttp_client(make_app(backend, creds, "t", demo=True, sessions=sessions, http_redirect=("127.0.0.1", unused_tcp_port, 443)))
        assert (await c.get("/api/session")).status == 200
    finally:
        busy.close()
