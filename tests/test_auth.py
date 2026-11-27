import os
import stat

from rnsd_webui.auth import Credentials, Sessions, hash_password, verify_password


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
