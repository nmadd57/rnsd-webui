"""Password login, sessions and CSRF protection. One administrator password; nothing else to manage."""
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
SESSION_SECONDS = 12 * 3600
MAX_FAILS, FAIL_WINDOW = 5, 60


def hash_password(password: str, salt: bytes = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${dk.hex()}"


PBKDF2_MAX_ITERATIONS = 2_000_000          # an upper bound so a stored hash cannot be used to make the login burn CPU


def pbkdf2_hash(password: str, salt: bytes = None, iterations: int = 200_000) -> str:
    """The format a browser can produce with WebCrypto (PBKDF2-HMAC-SHA256), used when the hash is made elsewhere,
    for example by a web page that sets this interface up: pbkdf2-sha256$<iterations>$<salt hex>$<hash hex>."""
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, dklen=32)
    return f"pbkdf2-sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if parts[0] == "scrypt" and len(parts) == 6:
            _, n, r, p, salt, dk = parts
            got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
            return hmac.compare_digest(got.hex(), dk)
        if parts[0] == "pbkdf2-sha256" and len(parts) == 4:
            _, iterations, salt, dk = parts
            iterations = int(iterations)
            if not 1 <= iterations <= PBKDF2_MAX_ITERATIONS:
                return False
            got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations, dklen=len(dk) // 2)
            return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        pass
    return False


class Credentials:
    """The stored password hash, in <data dir>/auth.json (mode 0600)."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "auth.json"

    def exists(self) -> bool:
        return self.path.exists()

    def set_password(self, password: str) -> None:
        self.set_hash(hash_password(password))

    def set_hash(self, stored: str) -> None:
        """Store a hash made elsewhere (scrypt or pbkdf2-sha256 format)."""
        if stored.split("$")[0] not in ("scrypt", "pbkdf2-sha256"):
            raise ValueError("unknown password hash format")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"password": stored}, f)
        os.replace(tmp, self.path)

    def check(self, password: str) -> bool:
        try:
            return verify_password(password, json.loads(self.path.read_text())["password"])
        except (OSError, ValueError, KeyError):
            return False

    def ensure(self) -> str:
        """First run: create a random password and return it (once) so the operator can read it from the log."""
        if self.exists():
            return ""
        password = secrets.token_urlsafe(12)
        self.set_password(password)
        return password


class Sessions:
    def __init__(self, now=time.time):
        self.now = now
        self.items = {}      # token -> {"csrf": str, "expires": float}
        self.fails = {}      # ip -> [timestamps]

    def create(self) -> dict:
        token = secrets.token_urlsafe(32)
        s = {"token": token, "csrf": secrets.token_urlsafe(24), "expires": self.now() + SESSION_SECONDS}
        self.items[token] = s
        return s

    def get(self, token):
        s = self.items.get(token or "")
        if not s:
            return None
        if s["expires"] < self.now():
            self.items.pop(token, None)
            return None
        return s

    def drop(self, token) -> None:
        self.items.pop(token or "", None)

    def throttled(self, ip: str) -> bool:
        cutoff = self.now() - FAIL_WINDOW
        recent = [t for t in self.fails.get(ip, []) if t > cutoff]
        self.fails[ip] = recent
        return len(recent) >= MAX_FAILS

    def failed(self, ip: str) -> None:
        self.fails.setdefault(ip, []).append(self.now())

    def succeeded(self, ip: str) -> None:
        self.fails.pop(ip, None)
