from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import DEV_SECRET_KEY, DEV_STORAGE_SECRET_KEY, Settings

ROOT = Path(__file__).resolve().parent.parent


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize("origins", [["*"], [], ["https://app.example.com", "*"]])
def test_production_rejects_wildcard_or_empty_cors(origins: list[str]) -> None:
    with pytest.raises(ValidationError, match="LUMINA_CORS_ORIGINS"):
        settings(environment="production", cors_origins=origins)


def test_cors_startup_error_does_not_leak_secrets() -> None:
    with pytest.raises(ValidationError) as error:
        settings(
            environment="production",
            cors_origins=["*"],
            secret_key="super-secret-signing-key-0123456789-abcdef",
            database_url="postgresql+asyncpg://lumina:db-password@db:5432/lumina",
        )
    assert "super-secret-signing-key" not in str(error.value)
    assert "db-password" not in str(error.value)


def test_production_accepts_explicit_origins() -> None:
    configured = settings(
        environment="production",
        cors_origins=["https://app.example.com"],
        secret_key="a-real-production-secret-key-0123456789",
        storage_secret_key="the-storage-provider-secret",
    )
    assert configured.cors_origins == ["https://app.example.com"]


def test_wildcard_is_allowed_outside_production() -> None:
    assert settings(environment="local", cors_origins=["*"]).cors_origins == ["*"]


def test_cors_origins_env_var_is_a_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "LUMINA_CORS_ORIGINS", '["http://localhost:3000", "https://app.example.com"]'
    )
    assert settings().cors_origins == ["http://localhost:3000", "https://app.example.com"]


def test_production_wildcard_from_env_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUMINA_ENVIRONMENT", "production")
    monkeypatch.setenv("LUMINA_CORS_ORIGINS", '["*"]')
    with pytest.raises(ValidationError, match="LUMINA_CORS_ORIGINS"):
        settings()


def test_production_refuses_the_development_secret_key() -> None:
    with pytest.raises(ValidationError, match="LUMINA_SECRET_KEY") as error:
        settings(environment="production", cors_origins=["https://app.example.com"])
    assert DEV_SECRET_KEY not in str(error.value)


def test_production_accepts_its_own_secret_key() -> None:
    configured = settings(
        environment="production",
        cors_origins=["https://app.example.com"],
        secret_key="a-real-production-secret-key-0123456789",
        storage_secret_key="the-storage-provider-secret",
    )
    assert configured.secret_key.get_secret_value().startswith("a-real")


def test_blank_secret_key_falls_back_to_the_development_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUMINA_SECRET_KEY", "  ")
    assert settings().secret_key.get_secret_value() == DEV_SECRET_KEY
    monkeypatch.setenv("LUMINA_ENVIRONMENT", "production")
    monkeypatch.setenv("LUMINA_CORS_ORIGINS", '["https://app.example.com"]')
    with pytest.raises(ValidationError, match="LUMINA_SECRET_KEY"):
        settings()


def test_production_refuses_the_local_storage_secret() -> None:
    with pytest.raises(ValidationError, match="LUMINA_STORAGE_SECRET_KEY") as error:
        settings(
            environment="production",
            cors_origins=["https://app.example.com"],
            secret_key="a-real-production-secret-key-0123456789",
        )
    assert DEV_STORAGE_SECRET_KEY not in str(error.value)


def test_local_storage_secret_is_fine_outside_production() -> None:
    assert settings(environment="staging").storage_secret_key.get_secret_value() == (
        DEV_STORAGE_SECRET_KEY
    )


def test_short_secret_key_is_rejected_without_echoing_it() -> None:
    with pytest.raises(ValidationError, match="at least 32") as error:
        settings(secret_key="too-short-secret")
    assert "too-short-secret" not in str(error.value)


def test_env_example_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented example must always load, so the docs cannot drift from the code."""
    for name in ("LUMINA_CORS_ORIGINS", "LUMINA_ENVIRONMENT", "LUMINA_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    example = Settings(_env_file=ROOT / ".env.example")
    assert example.cors_origins == ["http://localhost:3000"]


def test_blank_provider_settings_mean_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LUMINA_GOOGLE_CLIENT_ID", "LUMINA_APPLE_PRIVATE_KEY", "LUMINA_ENCRYPTION_KEY"):
        monkeypatch.setenv(name, "")
    configured = settings()
    assert configured.google_client_id is None
    assert configured.apple_private_key is None
    assert configured.encryption_key is None


def test_providers_are_enabled_only_when_fully_configured() -> None:
    import httpx

    from app.modules.identity.oauth import OAuthProviders

    http = httpx.AsyncClient()
    half = settings(google_client_id="id-only", apple_client_id="x", apple_team_id="t")
    assert OAuthProviders(half, http).enabled() == []
    full = settings(google_client_id="id", google_client_secret="secret")
    assert OAuthProviders(full, http).enabled() == ["google"]
