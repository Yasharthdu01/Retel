import hashlib
import hmac
import time

from app.core.security import verify_timestamped_retell_signature


def test_retell_signature_and_replay_protection():
    body = b'{"event":"call_started"}'
    timestamp = str(int(time.time() * 1000))
    digest = hmac.new(b"secret", body + timestamp.encode(), hashlib.sha256).hexdigest()
    assert verify_timestamped_retell_signature(body, f"v={timestamp},d={digest}", "secret")
    stale = str(int(timestamp) - 301_000)
    stale_digest = hmac.new(b"secret", body + stale.encode(), hashlib.sha256).hexdigest()
    assert not verify_timestamped_retell_signature(body, f"v={stale},d={stale_digest}", "secret")
