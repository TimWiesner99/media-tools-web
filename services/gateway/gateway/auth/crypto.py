"""Password hashing utilities.

Flow:
  Browser  →  sha256(plaintext)  →  sends 64-char hex string to server
  Server   →  bcrypt(sha256_hex) →  stores in DB

The server never sees the plaintext password. The bcrypt layer adds a
server-side salt and slow KDF on top of the client-side SHA-256.

For the initial admin account created from the ADMIN_PASSWORD env var,
we simulate what the browser would send by computing SHA-256 in Python
(hashlib), then bcrypt that result — identical to the browser path.

Uses bcrypt directly (passlib 1.7 has incompatibilities with bcrypt >= 4.0).
"""

import hashlib
import hmac

import bcrypt


def hash_password(sha256_hex: str) -> str:
    """Hash the client-provided SHA-256 hex string with bcrypt.

    The SHA-256 hex string is always 64 characters (well under bcrypt's 72-byte limit).
    """
    return bcrypt.hashpw(sha256_hex.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(sha256_hex: str, hashed: str) -> bool:
    """Constant-time bcrypt verification."""
    return bcrypt.checkpw(sha256_hex.encode("utf-8"), hashed.encode("utf-8"))


def sha256_hex_of(plaintext: str) -> str:
    """Compute SHA-256 hex of a plaintext string (server-side, for admin init).

    This mirrors exactly what the browser's Web Crypto API sends:
        crypto.subtle.digest('SHA-256', new TextEncoder().encode(plaintext))
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
