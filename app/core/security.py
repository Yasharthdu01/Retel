import hashlib
import hmac
import re
import time


def verify_retell_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


def verify_timestamped_retell_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    match = re.fullmatch(r"v=(\d+),d=([0-9a-fA-F]+)", signature)
    if not match:
        return False
    timestamp, digest = match.groups()
    if abs(int(time.time() * 1000) - int(timestamp)) > 5 * 60 * 1000:
        return False
    expected = hmac.new(secret.encode(), payload + timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest.lower())
