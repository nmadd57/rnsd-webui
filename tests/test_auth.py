import os
import stat

import pytest

from rnsd_webui.auth import Credentials, Sessions, hash_password, pbkdf2_hash, verify_password


def test_hash_is_salted_and_verifies():
    a, b = hash_password("secret-pass"), hash_password("secret-pass")
    assert a != b and a.startswith("scrypt$")
    assert verify_password("secret-pass", a) and not verify_password("wrong", a)
    assert not verify_password("secret-pass", "garbage") and not verify_password("x", "md5$1$2$3$aa$bb")


def test_credentials_file_is_private_and_first_run_creates_a_password(tmp_path):
    c = Credentials(tmp_path / "d")
    assert not c.exists() and not c.check("anything")
    first = c.ensure()
    assert len(first) >= 12 and c.check(first)
    assert c.ensure() == ""                                     # only the first run creates one
    assert stat.S_IMODE(os.stat(c.path).st_mode) == 0o600
    c.set_password("another-one-123")
    assert c.check("another-one-123") and not c.check(first)


def test_sessions_expire_and_drop():
    t = [1000.0]
    s = Sessions(now=lambda: t[0])
    one = s.create()
    assert s.get(one["token"])["csrf"] == one["csrf"]
    t[0] += 13 * 3600
    assert s.get(one["token"]) is None
    two = s.create()
    s.drop(two["token"])
    assert s.get(two["token"]) is None and s.get(None) is None


def test_login_throttle_window():
    t = [0.0]
    s = Sessions(now=lambda: t[0])
    for _ in range(5):
        assert not s.throttled("1.2.3.4")
        s.failed("1.2.3.4")
    assert s.throttled("1.2.3.4") and not s.throttled("5.6.7.8")
    t[0] += 61
    assert not s.throttled("1.2.3.4")
    s.failed("1.2.3.4"); s.succeeded("1.2.3.4")
    assert not s.throttled("1.2.3.4")


def test_pbkdf2_hashes_made_elsewhere_verify():
    h = pbkdf2_hash("browser-made-pw", salt=bytes.fromhex("00112233445566778899aabbccddeeff"), iterations=1000)
    assert h == "pbkdf2-sha256$1000$00112233445566778899aabbccddeeff$" + h.rsplit("$", 1)[1]
    assert verify_password("browser-made-pw", h) and not verify_password("other", h)
    assert not verify_password("x", "pbkdf2-sha256$99999999$00$00")           # absurd iteration counts are refused
    assert not verify_password("x", "pbkdf2-sha256$0$00$00") and not verify_password("x", "pbkdf2-sha256$abc$00$00")


def test_a_ready_made_hash_can_be_stored(tmp_path):
    c = Credentials(tmp_path / "d")
    c.set_hash(pbkdf2_hash("from-the-installer", iterations=1000))
    assert c.check("from-the-installer") and not c.check("nope")
    with pytest.raises(ValueError):
        c.set_hash("md5$whatever")
