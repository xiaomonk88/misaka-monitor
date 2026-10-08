"""密码哈希（标准库 pbkdf2，无需第三方依赖）。"""
from __future__ import annotations

import hashlib
import hmac
import os

_ALGO = "sha256"
_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(_ALGO, password.encode("utf-8"), salt, _ITER)
    return f"pbkdf2${_ALGO}${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, algo, iters, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False
