import pytest

from rnsd_webui.backend import BackendError, check_hash, jsonable, printable


def test_jsonable_converts_bytes_recursively():
    out = jsonable({"a": b"\x01\xff", "b": [b"\x00", {"c": (b"\x10",)}], "n": float("nan"), "ok": 3})
    assert out == {"a": "01ff", "b": ["00", {"c": ["10"]}], "n": None, "ok": 3}


def test_printable_prefers_text_and_falls_back_to_hex():
    assert printable(b"hello") == {"text": "hello", "hex": "68656c6c6f"}
    assert printable(b"\xff\xfe")["text"] is None
    assert printable(b"a\x00b")["text"] is None           # control characters are not shown as text
    assert printable(None) == {"text": "", "hex": ""}


def test_check_hash():
    assert check_hash("  " + "0f" * 16 + " ") == bytes.fromhex("0f" * 16)
    for bad in ("", "0f" * 15, "0g" * 16, None):
        with pytest.raises(BackendError):
            check_hash(bad)
