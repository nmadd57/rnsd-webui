"""Against a real rnsd. Skipped when the rns package is missing."""
import asyncio
import socket
import subprocess
import sys
import textwrap
import time

import pytest

RNS = pytest.importorskip("RNS")
pytestmark = pytest.mark.integration


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def rnsd(tmp_path_factory):
    d = tmp_path_factory.mktemp("rns")
    shared, control, listen = free_port(), free_port(), free_port()
    (d / "config").write_text(textwrap.dedent(f"""
        [reticulum]
          enable_transport = yes
          share_instance = yes
          shared_instance_type = tcp
          shared_instance_port = {shared}
          instance_control_port = {control}
        [logging]
          loglevel = 2
        [interfaces]
          [[Test TCP]]
            type = TCPServerInterface
            enabled = yes
            listen_ip = 127.0.0.1
            listen_port = {listen}
    """))
    proc = subprocess.Popen([sys.executable, "-m", "RNS.Utilities.rnsd", "--config", str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", shared), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.3)
    else:
        proc.kill()
        pytest.fail("rnsd did not start")
    yield d
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def shared_backend(rnsd, tmp_path_factory):
    """Reticulum is a singleton per process, so one backend serves the whole module (built on the main thread: Reticulum
    installs signal handlers)."""
    from rnsd_webui.backend import RnsBackend
    b = RnsBackend(str(rnsd), tmp_path_factory.mktemp("data"))
    yield b
    b.close()


@pytest.fixture()
async def backend(shared_backend):
    shared_backend.bind_loop(asyncio.get_running_loop())
    return shared_backend


async def test_status_lists_the_real_interfaces(backend):
    s = await backend.status()
    names = {i["type"] for i in s["interfaces"]}
    assert "TCPServerInterface" in names and "LocalServerInterface" in names
    assert s["transport"]["id"] and len(s["transport"]["id"]) == 32
    assert all(isinstance(i["rxb"], int) for i in s["interfaces"])


async def test_announce_shows_up_in_the_path_table(backend):
    me = backend.self_info()
    await backend.announce("integration test")
    for _ in range(60):
        paths = await backend.paths()
        if any(p["hash"] == me["destination"] for p in paths):
            break
        await asyncio.sleep(0.5)
    row = next(p for p in paths if p["hash"] == me["destination"])
    assert row["hops"] == 0 and row["interface"]


async def test_the_identity_is_stored_privately(backend):
    import os
    import stat
    path = [p for p in __import__("pathlib").Path(backend.identity_path).parent.glob("identity")][0]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and len(backend.self_info()["destination"]) == 32


async def test_request_path_is_accepted(backend):
    assert (await backend.request_path("ab" * 16))["requested"] == "ab" * 16


async def test_an_announce_from_another_client_reaches_the_feed(rnsd, backend):
    peer = textwrap.dedent(f"""
        import RNS, time
        r = RNS.Reticulum(configdir={str(rnsd)!r}, require_shared_instance=True)
        d = RNS.Destination(RNS.Identity(), RNS.Destination.IN, RNS.Destination.SINGLE, "peer", "app")
        time.sleep(1)
        d.announce(app_data=b"from the peer")
        print(d.hash.hex(), flush=True)
        time.sleep(15)
    """)
    p = await asyncio.create_subprocess_exec(sys.executable, "-c", peer, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    line = (await asyncio.wait_for(p.stdout.readline(), 20)).decode().strip()
    for _ in range(60):
        if any(a["destination"] == line for a in backend.announces):
            break
        await asyncio.sleep(0.5)
    p.kill()
    await p.wait()
    got = [a for a in backend.announces if a["destination"] == line]
    assert got and got[0]["app_data"]["text"] == "from the peer"


def test_it_refuses_to_start_without_a_running_rnsd(tmp_path):
    """A client must never start an rnsd of its own (it would grab the radio). Run in a fresh process: Reticulum is a singleton."""
    cfg = tmp_path / "idle"
    cfg.mkdir()
    (cfg / "config").write_text("[reticulum]\n  share_instance = yes\n  shared_instance_type = tcp\n  shared_instance_port = %d\n  instance_control_port = %d\n[interfaces]\n" % (free_port(), free_port()))
    r = subprocess.run([sys.executable, "-m", "rnsd_webui", "--rns-config", str(cfg), "--data-dir", str(tmp_path / "d"), "--listen", "127.0.0.1:%d" % free_port()],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 3 and "could not connect to a running rnsd" in r.stderr
