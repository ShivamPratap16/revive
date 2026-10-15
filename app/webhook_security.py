"""Signing and verifying webhooks (the same scheme Stripe uses).

Our webhook URL is public: anyone can POST to it. Without verification, an attacker
could send "call.completed" for every user, or replay an old genuine request. So the
provider signs each request with a secret only it and we know:

    X-Voice-Signature: t=1760512345,v1=5f2b...e9

    v1 = HMAC-SHA256(secret, f"{t}.{raw_body}")

  * HMAC proves the sender knows the secret and the body wasn't changed in transit.
  * The timestamp is inside the signed message, so it can't be edited, and we reject
    anything older than 5 minutes. A captured request can't be replayed tomorrow.
  * We sign the RAW bytes. Parsing the JSON and re-serialising it can change spacing or
    key order, and then a genuine signature no longer matches.
  * Several secrets can be valid at once, so the secret can be rotated without downtime:
    add the new one, switch the provider over, then remove the old one.
"""

import hashlib
import hmac
from datetime import datetime


class WebhookSignatureError(Exception):
    pass


def sign(secret: str, timestamp: int, raw_body: bytes) -> str:
    message = f"{timestamp}.".encode() + raw_body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _parse(header: str) -> tuple[int, list[str]]:
    timestamp, signatures = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = int(value) if value.isdigit() else None
        elif key == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise WebhookSignatureError("malformed signature header")
    return timestamp, signatures


def verify(
    raw_body: bytes, header: str | None, secrets: list[str], now: datetime, tolerance: int = 300
) -> None:
    """Raise WebhookSignatureError unless the request is genuine and fresh."""
    if not header:
        raise WebhookSignatureError("missing signature header")
    timestamp, signatures = _parse(header)

    # abs(): reject timestamps far in the future too, not just old ones.
    if abs(now.timestamp() - timestamp) > tolerance:
        raise WebhookSignatureError("timestamp outside tolerance (possible replay)")

    for secret in secrets:
        expected = sign(secret, timestamp, raw_body).split("v1=", 1)[1]
        # compare_digest: constant-time, so timing doesn't leak how many characters matched.
        if any(hmac.compare_digest(expected, sig) for sig in signatures):
            return
    raise WebhookSignatureError("signature mismatch")
