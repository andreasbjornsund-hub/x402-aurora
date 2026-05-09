"""CDP (Coinbase Developer Platform) facilitator JWT auth.

Used when FACILITATOR_URL points to cdp.coinbase.com. The CDP facilitator
expects an Ed25519-signed JWT per request. Required env:

    CDP_API_KEY_NAME    — UUID-ish key id from the CDP dashboard
    CDP_API_KEY_SECRET  — base64-encoded Ed25519 seed (from the CDP dashboard)

If those vars aren't set, create_cdp_auth_provider() returns None and the
caller should fall back to the no-auth public x402.org facilitator.
"""
import base64
import os
import time
import uuid
from typing import Callable

import jwt


def _build_jwt(method: str, host: str, path: str) -> str:
    """Build an Ed25519-signed JWT for a single CDP API request."""
    key_id = os.getenv("CDP_API_KEY_NAME")
    key_secret = os.getenv("CDP_API_KEY_SECRET")
    if not key_id or not key_secret:
        raise RuntimeError("CDP_API_KEY_NAME / CDP_API_KEY_SECRET not set")

    # CDP secret format: base64-encoded Ed25519 seed (32 bytes)
    seed = base64.b64decode(key_secret)
    if len(seed) != 32:
        raise RuntimeError(f"Expected 32-byte Ed25519 seed, got {len(seed)}")

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private_key = Ed25519PrivateKey.from_private_bytes(seed)

    now = int(time.time())
    payload = {
        "iss": "coinbase-cloud",
        "sub": key_id,
        "nbf": now,
        "exp": now + 120,  # 2-minute window
        "uri": f"{method} {host}{path}",
    }
    headers = {"kid": key_id, "nonce": uuid.uuid4().hex}
    return jwt.encode(payload, private_key, algorithm="EdDSA", headers=headers)


def create_cdp_auth_provider() -> Callable | None:
    """Return an async function that signs CDP requests, or None if unconfigured."""
    if not os.getenv("CDP_API_KEY_NAME") or not os.getenv("CDP_API_KEY_SECRET"):
        return None

    async def auth_provider(method: str, url: str) -> dict:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        token = _build_jwt(method, parsed.netloc, parsed.path or "/")
        return {"Authorization": f"Bearer {token}"}

    return auth_provider
