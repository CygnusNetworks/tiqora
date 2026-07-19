"""Settings and engine URL normalisation tests."""

from tiqora.config import Settings
from tiqora.db.engine import _normalize_url


def test_settings_defaults() -> None:
    s = Settings()
    assert s.app_name == "Tiqora"
    assert "localhost" in s.database_url
    assert s.redis_url.startswith("redis://")
    assert s.schema_ownership is False


def test_cors_list() -> None:
    s = Settings(cors_origins="http://a, http://b")
    assert s.cors_origin_list == ["http://a", "http://b"]


def test_normalize_url_postgres() -> None:
    assert _normalize_url("postgresql://u:p@h/db").startswith("postgresql+asyncpg://")
    assert _normalize_url("postgres://u:p@h/db").startswith("postgresql+asyncpg://")


def test_normalize_url_mysql() -> None:
    assert _normalize_url("mysql://u:p@h/db").startswith("mysql+aiomysql://")
    assert _normalize_url("mariadb://u:p@h/db").startswith("mysql+aiomysql://")
