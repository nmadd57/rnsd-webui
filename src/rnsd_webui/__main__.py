"""Command line: `rnsd-webui` serves the interface; `rnsd-webui set-password` changes the login password."""
import argparse
import getpass
import logging
import os
import ssl
import sys
from pathlib import Path

from aiohttp import web

from . import __version__
from .app import make_app
from .auth import Credentials
from .backend import BackendError, FakeBackend


def default_data_dir():
    return Path("/var/lib/rnsd-webui") if os.geteuid() == 0 else Path.home() / ".local" / "share" / "rnsd-webui"


def parser():
    ap = argparse.ArgumentParser(prog="rnsd-webui", description="A web interface for a running Reticulum daemon (rnsd).")
    ap.add_argument("command", nargs="?", choices=["serve", "set-password"], default="serve")
    ap.add_argument("--listen", default="127.0.0.1:8080", help="address:port to listen on (default 127.0.0.1:8080)")
    ap.add_argument("--rns-config", default=None, help="Reticulum config directory (default: the standard one, e.g. /etc/reticulum)")
    ap.add_argument("--data-dir", default=None, help="where the password hash and this interface's own identity are kept")
    ap.add_argument("--tls-cert", default=None)
    ap.add_argument("--tls-key", default=None)
    ap.add_argument("--demo", action="store_true", help="serve made-up data instead of talking to rnsd (to try the interface)")
    ap.add_argument("--version", action="version", version=f"rnsd-webui {__version__}")
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("rnsd-webui")
    data_dir = Path(a.data_dir) if a.data_dir else default_data_dir()
    creds = Credentials(data_dir)

    if a.command == "set-password":
        pw = getpass.getpass("New password: ")
        if len(pw) < 8 or pw != getpass.getpass("Again: "):
            print("The password must be at least 8 characters and typed the same twice.", file=sys.stderr)
            return 1
        creds.set_password(pw)
        print("Password changed.")
        return 0

    first = creds.ensure()
    if first:
        log.warning("first run: the login password is %s (change it with: rnsd-webui set-password)", first)

    host, _, port = a.listen.rpartition(":")
    try:
        port = int(port)
    except ValueError:
        print("--listen must look like 127.0.0.1:8080", file=sys.stderr)
        return 2

    if a.demo:
        backend = FakeBackend()
    else:
        from .backend import RnsBackend
        try:
            backend = RnsBackend(a.rns_config, data_dir)
        except BackendError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3

    tls = None
    if a.tls_cert:
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.load_cert_chain(a.tls_cert, a.tls_key)
    elif host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("listening on %s without TLS: the password and session cookie cross the network in clear text. "
                    "Use --tls-cert/--tls-key or put it behind a TLS proxy.", host)

    web.run_app(make_app(backend, creds, __version__, demo=a.demo), host=host or "127.0.0.1", port=port, ssl_context=tls,
                print=lambda s: log.info(s.strip()), handle_signals=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
