from app.core.config import normalize_database_url


def test_neon_url_is_accepted_as_copied():
    neon = ("postgresql://user:pw@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb"
            "?sslmode=require&channel_binding=require")
    assert normalize_database_url(neon) == (
        "postgresql+asyncpg://user:pw@ep-x.ap-southeast-1.aws.neon.tech/neondb?ssl=require")


def test_other_forms():
    assert normalize_database_url("postgres://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    already = "postgresql+asyncpg://u:p@h/db?ssl=require"
    assert normalize_database_url(already) == already
    assert normalize_database_url("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"
