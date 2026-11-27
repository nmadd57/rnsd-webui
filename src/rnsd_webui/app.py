"""The aiohttp application: JSON API, server-sent announce stream, and the static page."""
import asyncio
import json
import secrets
from pathlib import Path

from aiohttp import web

from .auth import Credentials, Sessions
from .backend import BackendError

STATIC = Path(__file__).with_name("static")
K_BACKEND = web.AppKey("backend")
K_CREDS = web.AppKey("creds")
K_SESSIONS = web.AppKey("sessions")
K_VERSION = web.AppKey("version", str)
K_DEMO = web.AppKey("demo", bool)
COOKIE = "rnsd_session"
PUBLIC = {"/api/login", "/api/session"}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
}


def json_response(data, status=200):
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, separators=(",", ":")))


def error(message, status):
    return json_response({"error": message}, status)


@web.middleware
async def security(request, handler):
    try:
        resp = await handler(request)
    except web.HTTPException as exc:
        resp = exc
    for k, v in SECURITY_HEADERS.items():
        resp.headers[k] = v
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@web.middleware
async def authenticate(request, handler):
    if not request.path.startswith("/api/") or request.path in PUBLIC:
        return await handler(request)
    session = request.app[K_SESSIONS].get(request.cookies.get(COOKIE))
    if not session:
        return error("sign in required", 401)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session["csrf"]):
            return error("missing or wrong CSRF token", 403)
    return await handler(request)


async def read_json(request):
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def secure_cookie(request):
    return request.secure or request.headers.get("X-Forwarded-Proto", "") == "https"


async def session_info(request):
    s = request.app[K_SESSIONS].get(request.cookies.get(COOKIE))
    return json_response({"authenticated": bool(s), "csrf": s["csrf"] if s else None,
                          "version": request.app[K_VERSION], "demo": request.app[K_DEMO]})


async def login(request):
    sessions, creds = request.app[K_SESSIONS], request.app[K_CREDS]
    ip = request.remote or "?"
    if sessions.throttled(ip):
        return error("too many attempts; wait a minute", 429)
    body = await read_json(request)
    password = body.get("password")
    if not isinstance(password, str) or not creds.check(password):
        sessions.failed(ip)
        await asyncio.sleep(0.5)
        return error("wrong password", 401)
    sessions.succeeded(ip)
    s = sessions.create()
    resp = json_response({"authenticated": True, "csrf": s["csrf"]})
    resp.set_cookie(COOKIE, s["token"], httponly=True, samesite="Strict", secure=secure_cookie(request), max_age=12 * 3600, path="/")
    return resp


async def logout(request):
    request.app[K_SESSIONS].drop(request.cookies.get(COOKIE))
    resp = json_response({"authenticated": False})
    resp.del_cookie(COOKIE, path="/")
    return resp


async def status(request):
    return json_response(await request.app[K_BACKEND].status())


async def paths(request):
    return json_response(await request.app[K_BACKEND].paths())


async def me(request):
    return json_response(request.app[K_BACKEND].self_info())


async def announces(request):
    return json_response(list(request.app[K_BACKEND].announces))


async def announce_stream(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    backend = request.app[K_BACKEND]
    q = backend.subscribe()
    try:
        await resp.write(b": connected\n\n")
        while True:
            try:
                entry = await asyncio.wait_for(q.get(), timeout=20)
                await resp.write(b"data: " + json.dumps(entry, separators=(",", ":")).encode() + b"\n\n")
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        backend.unsubscribe(q)
    return resp


async def request_path(request):
    body = await read_json(request)
    try:
        return json_response(await request.app[K_BACKEND].request_path(body.get("hash")))
    except BackendError as exc:
        return error(str(exc), 400)


async def announce(request):
    body = await read_json(request)
    data = body.get("app_data", "")
    if not isinstance(data, str) or len(data.encode("utf-8")) > 300:
        return error("announce data must be text of at most 300 bytes", 400)
    try:
        return json_response(await request.app[K_BACKEND].announce(data))
    except BackendError as exc:
        return error(str(exc), 400)


async def index(request):
    return web.FileResponse(STATIC / "index.html")


def make_app(backend, creds: Credentials, version="0.1.0", demo=False, sessions=None):
    app = web.Application(middlewares=[security, authenticate], client_max_size=64 * 1024)
    app[K_BACKEND], app[K_CREDS], app[K_SESSIONS] = backend, creds, sessions or Sessions()
    app[K_VERSION], app[K_DEMO] = version, demo
    r = app.router
    r.add_get("/api/session", session_info)
    r.add_post("/api/login", login)
    r.add_post("/api/logout", logout)
    r.add_get("/api/status", status)
    r.add_get("/api/paths", paths)
    r.add_get("/api/self", me)
    r.add_get("/api/announces", announces)
    r.add_get("/api/announces/stream", announce_stream)
    r.add_post("/api/path-request", request_path)
    r.add_post("/api/announce", announce)
    r.add_get("/", index)
    r.add_static("/static/", STATIC, show_index=False)

    async def on_startup(a):
        a[K_BACKEND].bind_loop(asyncio.get_running_loop())

    async def on_cleanup(a):
        a[K_BACKEND].close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app
