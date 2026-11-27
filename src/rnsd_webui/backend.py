"""What the web interface asks of Reticulum. `RnsBackend` talks to a running rnsd; `FakeBackend` gives the same shapes
with made-up data (for tests and `--demo`)."""
import asyncio
import logging
import re
import time
from collections import deque

HASH_RE = re.compile(r"^[0-9a-fA-F]{32}$")
MAX_ANNOUNCES = 300
log = logging.getLogger("rnsd-webui")


class BackendError(Exception):
    """A request the backend cannot do; the message is safe to show to the user."""


def jsonable(value):
    """bytes become hex strings, everything else is made JSON safe, recursively."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, float) and value != value:
        return None
    return value


def printable(data):
    """Announce data as text when it is text, as hex otherwise."""
    if not data:
        return {"text": "", "hex": ""}
    try:
        text = bytes(data).decode("utf-8")
        if text.isprintable():
            return {"text": text, "hex": bytes(data).hex()}
    except UnicodeDecodeError:
        pass
    return {"text": None, "hex": bytes(data).hex()}


class BaseBackend:
    def __init__(self):
        self.announces = deque(maxlen=MAX_ANNOUNCES)
        self.subscribers = set()
        self.loop = None

    # ---- announce feed
    def bind_loop(self, loop):
        self.loop = loop

    def record_announce(self, entry):
        """Safe to call from any thread."""
        def deliver():
            # Reticulum can report the same announce twice in a row, once without its data (in either order): keep one,
            # the one with the data.
            if self.announces:
                last = self.announces[0]
                same = last["destination"] == entry["destination"] and abs(entry["time"] - last["time"]) < 3
                last_has, new_has = bool((last["app_data"] or {}).get("hex")), bool((entry["app_data"] or {}).get("hex"))
                if same and not new_has and last_has:
                    return
                if same and new_has and not last_has:
                    self.announces.popleft()
            self.announces.appendleft(entry)
            for q in list(self.subscribers):
                try:
                    q.put_nowait(entry)
                except asyncio.QueueFull:
                    pass
        if self.loop:
            self.loop.call_soon_threadsafe(deliver)
        else:
            deliver()

    def subscribe(self):
        q = asyncio.Queue(maxsize=100)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self.subscribers.discard(q)

    # ---- to implement
    async def status(self):
        raise NotImplementedError

    async def paths(self):
        raise NotImplementedError

    async def request_path(self, hash_hex):
        raise NotImplementedError

    async def announce(self, app_data):
        raise NotImplementedError

    def self_info(self):
        raise NotImplementedError

    def close(self):
        pass


def check_hash(hash_hex):
    if not isinstance(hash_hex, str) or not HASH_RE.match(hash_hex.strip()):
        raise BackendError("a destination hash is 32 hexadecimal characters")
    return bytes.fromhex(hash_hex.strip())


class RnsBackend(BaseBackend):
    """A client of the shared instance of a running rnsd. It refuses to start an instance of its own: a second instance
    would try to take over the radio and the interfaces."""

    ASPECTS = ("rnsd_webui", "node")

    def __init__(self, config_dir, data_dir, display_name="rnsd-webui"):
        super().__init__()
        import RNS
        self.RNS = RNS
        try:
            self.reticulum = RNS.Reticulum(configdir=config_dir, require_shared_instance=True)
        except Exception as exc:
            raise BackendError(
                f"could not connect to a running rnsd with the config in {config_dir or 'the default location'}: {exc}. "
                "Is rnsd running, and does its config have share_instance = yes?") from exc
        if not self.reticulum.is_connected_to_shared_instance:
            raise BackendError("not connected to a shared rnsd instance; refusing to start a second instance")
        self.display_name = display_name
        self.identity_path = str(__import__("pathlib").Path(data_dir) / "identity")
        self.identity = self._load_identity(data_dir)
        self.destination = RNS.Destination(self.identity, RNS.Destination.IN, RNS.Destination.SINGLE, *self.ASPECTS)
        self.started = time.time()

        backend = self

        class Handler:
            aspect_filter = None

            # Reticulum calls this with keyword arguments, and newer versions add some (announce_packet_hash, ...):
            # accept whatever it passes.
            def received_announce(self, destination_hash=None, announced_identity=None, app_data=None, **_ignored):
                try:
                    backend._on_announce(destination_hash, announced_identity, app_data)
                except Exception:
                    log.exception("could not record an announce")

        self._handler = Handler()
        RNS.Transport.register_announce_handler(self._handler)

    def _load_identity(self, data_dir):
        from pathlib import Path
        RNS = self.RNS
        path = Path(data_dir) / "identity"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            ident = RNS.Identity.from_file(str(path))
            if ident:
                return ident
        ident = RNS.Identity()
        ident.to_file(str(path))
        path.chmod(0o600)
        return ident

    def _on_announce(self, destination_hash, announced_identity, app_data):
        RNS = self.RNS
        try:
            hops = RNS.Transport.hops_to(destination_hash)
        except Exception:
            hops = None
        self.record_announce({
            "time": time.time(),
            "destination": destination_hash.hex(),
            "identity": announced_identity.hash.hex() if announced_identity else None,
            "hops": hops if hops is not None and hops < 128 else None,
            "app_data": printable(app_data),
        })

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    async def status(self):
        st = await self._run(self.reticulum.get_interface_stats)
        keep = ("name", "short_name", "type", "status", "mode", "bitrate", "rxb", "txb", "rxs", "txs", "clients",
                "incoming_announce_frequency", "outgoing_announce_frequency", "held_announces", "ifac_signature")
        interfaces = []
        for i in st.get("interfaces", []):
            row = {k: i.get(k) for k in keep if k in i}
            row["ifac"] = bool(i.get("ifac_signature"))
            row.pop("ifac_signature", None)
            interfaces.append(row)
        links = await self._run(self.reticulum.get_link_count)
        return jsonable({
            "transport": {"id": st.get("transport_id"), "uptime": st.get("transport_uptime"), "network_id": st.get("network_id")},
            "totals": {"rxb": st.get("rxb"), "txb": st.get("txb"), "rxs": st.get("rxs"), "txs": st.get("txs")},
            "links": links,
            "interfaces": interfaces,
            "time": time.time(),
        })

    async def paths(self):
        table = await self._run(self.reticulum.get_path_table)
        now = time.time()
        rows = []
        for p in table:
            rows.append({
                "hash": p.get("hash"), "via": p.get("via"), "hops": p.get("hops"), "interface": p.get("interface"),
                "age": max(0, now - p["timestamp"]) if p.get("timestamp") else None,
                "expires_in": max(0, p["expires"] - now) if p.get("expires") else None,
            })
        rows.sort(key=lambda r: (r["hops"] if r["hops"] is not None else 999, r["hash"] or b""))
        return jsonable(rows)

    async def request_path(self, hash_hex):
        h = check_hash(hash_hex)
        await self._run(self.RNS.Transport.request_path, h)
        return {"requested": h.hex()}

    async def announce(self, app_data):
        data = (app_data or self.display_name).encode("utf-8")[:300]
        await self._run(self.destination.announce, data)
        return {"destination": self.destination.hash.hex(), "app_data": data.decode("utf-8", "replace")}

    def self_info(self):
        return {"destination": self.destination.hash.hex(), "identity": self.identity.hash.hex(),
                "name": ".".join(self.ASPECTS), "connected": True}

    def close(self):
        try:
            self.RNS.Transport.deregister_announce_handler(self._handler)
        except Exception:
            pass


class FakeBackend(BaseBackend):
    """Plausible data with the same shapes, for tests and for trying the interface without a daemon."""

    def __init__(self):
        super().__init__()
        self.requested = []
        self.announced = []
        self.t0 = time.time()
        self.tx = 0

    async def status(self):
        self.tx += 1337
        return {
            "transport": {"id": "ab" * 16, "uptime": time.time() - self.t0, "network_id": None},
            "totals": {"rxb": 523000 + self.tx, "txb": 214000 + self.tx, "rxs": 120.0, "txs": 48.0},
            "links": 2,
            "interfaces": [
                {"name": "SX1302Interface[SX1302 LoRa]", "short_name": "SX1302 LoRa", "type": "SX1302Interface", "status": True, "mode": 1,
                 "bitrate": 3125, "rxb": 12000 + self.tx, "txb": 9000, "rxs": 12.0, "txs": 3.0, "clients": None,
                 "incoming_announce_frequency": 0.4, "outgoing_announce_frequency": 0.1, "held_announces": 0, "ifac": False},
                {"name": "AutoInterface[Default Interface]", "short_name": "Default Interface", "type": "AutoInterface", "status": True, "mode": 1,
                 "bitrate": 10000000, "rxb": 511000, "txb": 205000, "rxs": 108.0, "txs": 45.0, "clients": None,
                 "incoming_announce_frequency": 1.2, "outgoing_announce_frequency": 0.2, "held_announces": 1, "ifac": False},
            ],
            "time": time.time(),
        }

    async def paths(self):
        return [
            {"hash": "11" * 16, "via": "22" * 16, "hops": 1, "interface": "SX1302Interface[SX1302 LoRa]", "age": 120.0, "expires_in": 3400.0},
            {"hash": "33" * 16, "via": "44" * 16, "hops": 3, "interface": "AutoInterface[Default Interface]", "age": 30.0, "expires_in": 3570.0},
        ]

    async def request_path(self, hash_hex):
        h = check_hash(hash_hex)
        self.requested.append(h.hex())
        return {"requested": h.hex()}

    async def announce(self, app_data):
        self.announced.append(app_data)
        return {"destination": "cd" * 16, "app_data": app_data or "rnsd-webui"}

    def self_info(self):
        return {"destination": "cd" * 16, "identity": "ef" * 16, "name": "rnsd_webui.node", "connected": True}
