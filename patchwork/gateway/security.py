"""
HMAC Signature verification for GitHub Webhook deliveries.
"""

import hmac
import hashlib
from typing import Optional


def verify_github_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    secret: str
) -> bool:
    """
    Verify GitHub X-Hub-Signature-256 header against the payload body.
    Format: sha256=<hex_digest>
    """
    if not secret:
        # If no secret configured in dev mode, allow pass-through
        return True

    if not signature_header:
        return False

    prefix, sep, signature = signature_header.partition("=")
    if prefix.lower() != "sha256" or not signature:
        return False

    mac = hmac.new(secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256)
    expected = mac.hexdigest()

    return hmac.compare_digest(expected, signature)
