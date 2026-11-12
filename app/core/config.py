from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Only for local development and tests. Production refuses to start with it.
DEV_SECRET_KEY = "dev-only-insecure-secret-key-change-me-0123456789"
# The local MinIO password from docker-compose.yml. Production refuses it too.
DEV_STORAGE_SECRET_KEY = "lumina-dev-storage-secret"
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    """Typed application settings, loaded from env vars (prefix ``LUMINA_``) or ``.env``.

    See ``.env.example`` for every variable and its format.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LUMINA_",
        extra="ignore",
        # Startup errors must never print the offending values: they can be secrets
        # (LUMINA_SECRET_KEY, SMTP password) or contain them (database URL).
        hide_input_in_errors=True,
    )

    app_name: str = "Lumina API"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    # Complex types are read from the environment as JSON, so this must be a JSON array:
    #   LUMINA_CORS_ORIGINS='["http://localhost:3000","https://app.example.com"]'
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    database_url: str = "postgresql+asyncpg://lumina:lumina@localhost:5432/lumina"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # --- Authentication -------------------------------------------------------------------
    # Signs the access tokens (HS256). At least 32 characters; must be changed in production.
    secret_key: SecretStr = SecretStr(DEV_SECRET_KEY)
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)
    impersonation_ttl_minutes: int = Field(default=15, ge=1, le=60)
    # Argon2id cost (OWASP minimum: 19 MiB memory, 2 iterations, 1 lane). Tests lower it.
    argon2_memory_cost_kib: int = Field(default=19_456, ge=8)
    argon2_time_cost: int = Field(default=2, ge=1)

    # Encrypts stored two-factor (TOTP) secrets. Unset: derived from secret_key, which then
    # must never be rotated without also setting this to the old derived value (see
    # docs/auth.md). Set it explicitly in production.
    encryption_key: SecretStr | None = None

    # --- Social sign-in (a provider is enabled once its credentials are set) --------------
    # Public address of this API: providers redirect to {api_public_url}/api/v1/auth/oauth/
    # {provider}/callback, which must be registered with each provider exactly like that.
    api_public_url: str = "http://localhost:8000"
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: SecretStr | None = None
    # "common" (work, school and personal accounts), "organizations", "consumers", or a
    # tenant id to allow one organisation only.
    microsoft_tenant: str = "common"
    apple_client_id: str | None = None  # the Services ID
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    apple_private_key: SecretStr | None = None  # the .p8 key (PEM), newlines allowed

    # --- Media storage (S3-compatible: MinIO locally, S3/R2/GCS in production) ------------
    storage_endpoint_url: str = "http://localhost:9000"  # how the API reaches the storage
    # How browsers reach it (upload and download links are signed for this host). Empty:
    # same as storage_endpoint_url. In Docker Compose the API uses http://minio:9000 but the
    # browser needs http://localhost:9000.
    storage_public_endpoint_url: str | None = None
    storage_bucket: str = "lumina-media"
    storage_region: str = "us-east-1"
    storage_access_key: str = "lumina"
    storage_secret_key: SecretStr = SecretStr(DEV_STORAGE_SECRET_KEY)
    # Optional CDN in front of the bucket for published content (e.g. https://cdn.example.com).
    media_cdn_base_url: str | None = None
    media_upload_url_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    media_download_url_ttl_seconds: int = Field(default=3600, ge=60, le=43_200)
    # Sites whose pages may be embedded in lessons (https only). JSON array, like CORS.
    embed_allowed_hosts: list[str] = Field(
        default_factory=lambda: [
            "www.youtube.com",
            "www.youtube-nocookie.com",
            "player.vimeo.com",
            "www.geogebra.org",
            "phet.colorado.edu",
            "www.desmos.com",
            "scratch.mit.edu",
            "docs.google.com",
        ]
    )

    # --- Email ----------------------------------------------------------------------------
    # Links in emails point at the web app, which calls the API with the token.
    frontend_url: str = "http://localhost:3000"
    email_from: str = "Lumina <no-reply@lumina.example>"
    # Defaults match Mailpit from docker-compose.yml (web UI: http://localhost:8025).
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    smtp_timeout_seconds: float = 10.0

    # Field validators (not model validators), so each error names only its own field.

    @field_validator("cors_origins")
    @classmethod
    def _production_requires_explicit_cors(
        cls, value: list[str], info: ValidationInfo
    ) -> list[str]:
        if info.data.get("environment") == "production" and (not value or "*" in value):
            raise ValueError(
                "LUMINA_CORS_ORIGINS must list explicit origins in production "
                '(a JSON array such as \'["https://app.example.com"]\'); "*" or an empty '
                "list is not allowed."
            )
        return value

    @field_validator(
        "encryption_key",
        "google_client_id",
        "google_client_secret",
        "microsoft_client_id",
        "microsoft_client_secret",
        "apple_client_id",
        "apple_team_id",
        "apple_key_id",
        "apple_private_key",
        "storage_public_endpoint_url",
        "media_cdn_base_url",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        # `LUMINA_GOOGLE_CLIENT_ID=` in .env means "not configured", not an empty id.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("secret_key", mode="before")
    @classmethod
    def _blank_secret_key_means_default(cls, value: object) -> object:
        # `LUMINA_SECRET_KEY=` (as in .env.example) falls back to the development key, which
        # works locally and is refused in production by the check below.
        if isinstance(value, str) and not value.strip():
            return DEV_SECRET_KEY
        return value

    @field_validator("secret_key")
    @classmethod
    def _secret_key_strength(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"LUMINA_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters."
            )
        if info.data.get("environment") == "production" and secret == DEV_SECRET_KEY:
            raise ValueError(
                "LUMINA_SECRET_KEY must be set to your own random value in production "
                '(python3 -c "import secrets; print(secrets.token_urlsafe(48))").'
            )
        return value

    @field_validator("storage_secret_key")
    @classmethod
    def _production_storage_secret(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        if (
            info.data.get("environment") == "production"
            and value.get_secret_value() == DEV_STORAGE_SECRET_KEY
        ):
            raise ValueError(
                "LUMINA_STORAGE_SECRET_KEY must be your storage provider's key in production, "
                "not the local MinIO password."
            )
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
