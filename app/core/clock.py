from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    """Stored timestamps are UTC. PostgreSQL returns them aware; SQLite (tests) returns them naive."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
