# rnsd-webui

A small web interface for a running [Reticulum](https://reticulum.network/) daemon (`rnsd`): see its interfaces and traffic,
browse the path table, watch announces arrive live, and announce or request a path from your browser. Built for a
[Bobcat Miner 300 running as a Reticulum LoRa node](https://github.com/nmadd57/bobcat-reticulum-node), but it works with any `rnsd`.

It is a **client of the daemon's shared instance**, not a second Reticulum: it never starts an instance of its own (which
would try to take over the radio and the interfaces) and refuses to run if no `rnsd` is there.

| Dashboard | Paths |
|---|---|
| Transport ID, uptime, links, total traffic with live rates, and one card per interface (status, type, bitrate, bytes in and out, announce rates, IFAC) | The path table with hops, next hop, interface and time to expiry, a filter, and a per-row *Refresh* |
| **Announces** | **Tools** |
| A live feed (server-sent events) of every announce the daemon sees, with the announce text when it is text | Announce this interface's own destination, or request a path to a destination hash |

## Install

It needs Python 3.9 or newer and a running `rnsd` with `share_instance = yes` (the default).

```
python3 -m venv /opt/rnsd-webui/venv
/opt/rnsd-webui/venv/bin/pip install git+https://github.com/nmadd57/rnsd-webui
```

Use the same Python environment as your `rnsd` if you can, so the Reticulum versions match.

## Run

```
rnsd-webui --rns-config /etc/reticulum --listen 127.0.0.1:8080
```

The first time it runs it creates a random login password and **prints it once in the log**. Change it with
`rnsd-webui set-password`. The password hash is kept in the data directory (`/var/lib/rnsd-webui` as root,
`~/.local/share/rnsd-webui` otherwise) in a file only its owner can read, next to this interface's own Reticulum identity.

To try the interface without a daemon, `rnsd-webui --demo` serves made-up data.

### As a service

`deploy/rnsd-webui.service` is a systemd unit with the usual hardening. Copy it to `/etc/systemd/system/`, adjust the paths, then
`systemctl enable --now rnsd-webui`.

### Reaching it from another device

The default is to listen on `127.0.0.1` only. To use it from your network, listen on `0.0.0.0:8080` **and use HTTPS**: pass
`--tls-cert` and `--tls-key`, or put it behind a reverse proxy that terminates TLS (it honours `X-Forwarded-Proto`). Without TLS,
the password and the session cookie cross the network in clear text, and the program says so when you start it that way.

## Security notes

- One administrator password, stored as a salted scrypt hash. Five wrong attempts in a minute from one address are refused.
- Sessions are random tokens in an `HttpOnly`, `SameSite=Strict` cookie (and `Secure` over HTTPS), valid for 12 hours.
- Every change (announce, path request) needs a per-session CSRF token as well as the cookie.
- A strict Content-Security-Policy, no inline scripts, `X-Frame-Options: DENY`, and no caching of API responses.
- The interface can announce and request paths on the network the daemon is part of. It cannot change the daemon's
  configuration or restart it.
- Reading the daemon's shared instance needs access to the Reticulum config directory, so the usual setup runs it as the
  same user as `rnsd` (root, on the Bobcat).

## API

All JSON under `/api/`. Everything except `session` and `login` needs a session; changes also need the `X-CSRF-Token` header
(from `GET /api/session` or the login response).

| Request | What it does |
|---|---|
| `GET /api/session` | Whether you are signed in, and your CSRF token |
| `POST /api/login` `{"password": ...}` / `POST /api/logout` | Start and end a session |
| `GET /api/status` | Transport info, totals, and every interface |
| `GET /api/paths` | The path table |
| `GET /api/announces`, `GET /api/announces/stream` | The recent announces, and a live event stream of new ones |
| `GET /api/self` | This interface's destination hash |
| `POST /api/announce` `{"app_data": "text"}` | Announce this interface's destination |
| `POST /api/path-request` `{"hash": "<32 hex>"}` | Ask the network for a path |

## Development

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
python -m pytest -q          # unit and API tests, and integration tests against a real rnsd it starts itself
rnsd-webui --demo            # the interface with made-up data
```

The integration tests start a real `rnsd` (TCP shared instance on free ports), connect a backend to it, make announces from a
second client process, and check that they show up in the path table and the live feed. There is no build step for the
front end: `src/rnsd_webui/static/` is plain HTML, CSS and JavaScript.

## License

MIT.
