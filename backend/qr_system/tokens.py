"""
QR token handling.

* Tokens are random (secrets.token_urlsafe) and carry NO personal data.
* The database stores
    - token_hash : keyed HMAC-SHA256 (used for every lookup / validation)
    - token_enc  : the token encrypted with Fernet, ONLY so the owner can
                   view / download the same permanent QR again later.
  The plain token is never stored.
* The hash is salted with the QR type, so a token of one type can never be
  looked up as another type.

Set QR_SECRET in backend/.env (any long random string) BEFORE first use and
never change it afterwards: changing it invalidates every issued QR.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("campuspath.qr")

TYPE_IDENTITY = "student_identity"
TYPE_REGISTRATION = "event_registration"
TYPE_PASS = "event_attendance_pass"
ALL_TYPES = (TYPE_IDENTITY, TYPE_REGISTRATION, TYPE_PASS)

_PREFIX = {
    TYPE_IDENTITY: "CPQ-ID",
    TYPE_REGISTRATION: "CPQ-ER",
    TYPE_PASS: "CPQ-AP",
}
_BY_PREFIX = {v: k for k, v in _PREFIX.items()}
_DEV_SECRET = "campuspath-dev-qr-secret-CHANGE-ME"


class TokenVault:
    def __init__(self, secret: Optional[str] = None):
        secret = secret or os.getenv("QR_SECRET")
        if not secret:
            log.warning(
                "QR_SECRET is not set - using an insecure development "
                "secret. Set QR_SECRET in backend/.env before going live."
            )
            secret = _DEV_SECRET
        raw = secret.encode("utf-8")
        self._hmac_key = hashlib.sha256(b"campuspath-qr-hmac|" + raw).digest()
        self._fernet = Fernet(
            base64.urlsafe_b64encode(
                hashlib.sha256(b"campuspath-qr-enc|" + raw).digest()
            )
        )

    @staticmethod
    def new_token(qr_type: str) -> str:
        return f"{_PREFIX[qr_type]}.{secrets.token_urlsafe(32)}"

    @staticmethod
    def type_of(token) -> Optional[str]:
        """QR type announced by the token prefix (a hint only - the real
        check is the hash lookup inside that type's own collection)."""
        if not isinstance(token, str):
            return None
        token = token.strip()
        prefix, sep, rest = token.partition(".")
        if not sep or len(rest) < 32 or len(token) > 200:
            return None
        return _BY_PREFIX.get(prefix)

    def hash(self, qr_type: str, token: str) -> str:
        message = f"{qr_type}|{token.strip()}".encode("utf-8")
        return hmac.new(self._hmac_key, message, hashlib.sha256).hexdigest()

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            log.error("Could not decrypt a stored QR token (QR_SECRET changed?)")
            return None