import os
import re
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_LOCALHOST_CORS_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
_DEV_DEFAULT_ENCRYPTION_MASTER_KEY = (
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)
_HEX_64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


def _is_localhost_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    return host in _LOCALHOST_HOSTS or host.endswith(".localhost")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"
    SEED_DEV_DATA: bool | None = None

    DATABASE_URL: str = (
        "postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod@localhost:5432/manta_db"
    )
    MIGRATION_DATABASE_URL: str | None = None
    REDIS_URL: str = "redis://localhost:6379"

    # OIDC / JWKS (Architecture Blueprint v3.0 §6.1)
    KEYCLOAK_URL: str = "http://localhost:8080"
    KEYCLOAK_REALM: str = "manta"
    OIDC_JWKS_URL: str | None = None  # explicit override for cloud IdPs
    OIDC_AUDIENCE: str = "manta-backend"
    OIDC_JWKS_CACHE_TTL_SECONDS: int = 21600  # 6 hours per §6.1
    OIDC_JWKS_HTTP_TIMEOUT_SECONDS: float = 5.0
    OIDC_JWKS_MIN_REFRESH_INTERVAL_SECONDS: float = 10.0  # Rate-limit cooldown for unknown kid

    # OIDC Issuer Configuration (Architecture Blueprint v3.0 §6.1)
    OIDC_ISSUER: str | None = None  # Explicit production issuer override
    OIDC_EXTRA_ISSUERS: str | None = None  # Comma-separated list of additional trusted issuers

    # CORS Configuration
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    CORS_ALLOW_ORIGIN_REGEX: str | None = None

    # Secret & Credential Encryption (SCRUM-321)
    # Master key (32-byte / 256-bit hex string) used for AES-256-GCM
    ENCRYPTION_MASTER_KEY: str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    # Optional JSON-encoded keyring for rotation: '{"primary": "hex...", "old-key": "hex..."}'
    ENCRYPTION_KEYRING_JSON: str | None = None
    ACTIVE_ENCRYPTION_KEY_ID: str = "primary"
    WEBHOOK_BASE_URL: str | None = None

    # Email / Invitations (SCRUM-314)
    SMTP_HOST: str = "mailpit"
    SMTP_PORT: int = 1025
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@manta.local"
    SMTP_START_TLS: bool = False
    WEB_BASE_URL: str = "http://localhost:3000"

    KC_SERVICE_CLIENT_ID: str = "manta-backend"
    KC_SERVICE_CLIENT_SECRET: str = ""
    KEYCLOAK_ADMIN: str = "admin"
    KEYCLOAK_ADMIN_PASSWORD: str = "admin"
    KEYCLOAK_ADMIN_REALM: str = "master"

    # Recovery Sweeper Settings (SCRUM-388)
    SWEEP_INTERVAL_SECONDS: float = 5.0
    SWEEP_THRESHOLD_SECONDS: float = 10.0
    SWEEP_BATCH_SIZE: int = 100
    SWEEPER_HEARTBEAT_FILE: str = "/tmp/sweeper.heartbeat"

    # Outbox Relay Settings (SRS v2.0 §3.5.2)
    OUTBOX_RELAY_HEARTBEAT_FILE: str = "/tmp/outbox_relay.heartbeat"

    # Scheduled Blocker Detector Settings (TASK-307-1 / US-71)
    BLOCKER_DETECTOR_ENABLED: bool = True
    BLOCKER_DETECTOR_INTERVAL_SECONDS: float = 900.0  # 15 minutes
    BLOCKER_INCIDENT_WINDOW_MINUTES: int = 60
    BLOCKER_INCIDENT_SPIKE_THRESHOLD: int = 3
    BLOCKER_TENANT_DISCOVERY_BATCH_SIZE: int = 500
    BLOCKER_DETECTOR_HEARTBEAT_FILE: str = "/tmp/blocker_detector.heartbeat"

    # Worker Prometheus Metrics Exporter (SCRUM-424)
    WORKER_METRICS_PORT: int = 9101

    # Prometheus Broker Metrics Scrape Settings (TASK-302-1 / US-49)
    METRICS_REDIS_ENABLED: bool = True
    METRICS_REDIS_TIMEOUT_SECONDS: float = 0.75
    METRICS_REDIS_SCAN_LIMIT: int = 1000

    # AI / LLM Endpoints (US-41 / US-62: Zero Data Retention & Local Inference)
    LOCAL_LLM_BASE_URL: str = "http://localhost:11434/v1"
    LOCAL_LLM_MODEL: str = "llama3.1:8b"
    LOCAL_EMBEDDING_MODEL: str = "bge-small-en-v1.5"
    CLOUD_ZDR_LLM_BASE_URL: str | None = None
    CLOUD_ZDR_API_KEY: str | None = None
    CLOUD_ZDR_LLM_MODEL: str = "gpt-4o-mini"
    CLOUD_ZDR_EMBEDDING_MODEL: str = "text-embedding-3-small"
    AI_REQUEST_TIMEOUT_SECONDS: float = 30.0
    AI_REINDEX_STATUS_TTL_SECONDS: int = 86400

    # Firebase Cloud Messaging (Mobile Push Dispatch)
    FCM_PROJECT_ID: str | None = None
    FCM_CREDENTIALS_JSON: str | None = None

    # Refactoring Sandbox Execution & SCIP Indexing (US-46)
    SANDBOX_RUNTIME: str = "runsc"
    SANDBOX_IMAGE_DIGEST: str = ""
    SCIP_STORAGE_DIR: str = "/tmp/manta_scip_indexes"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in {"prod", "production"}

    @property
    def cors_origins(self) -> list[str]:
        return [orig.strip() for orig in self.CORS_ORIGINS.split(",") if orig.strip()]

    @property
    def cors_allow_origin_regex(self) -> str | None:
        cleaned = (self.CORS_ALLOW_ORIGIN_REGEX or "").strip()
        if cleaned:
            return cleaned
        if self.is_production:
            return None
        return _DEV_LOCALHOST_CORS_REGEX

    def validate_production_security(self) -> list[str]:
        """Validate mandatory production hardening invariants when APP_ENV=production."""
        if not self.is_production:
            return []

        errors: list[str] = []

        master_key = (self.ENCRYPTION_MASTER_KEY or "").strip()
        if (
            not _HEX_64_RE.match(master_key)
            or master_key.lower() == _DEV_DEFAULT_ENCRYPTION_MASTER_KEY
        ):
            errors.append(
                "ENCRYPTION_MASTER_KEY must be a rotated 64-character hex key (not the dev default)."
            )

        kc_pass = (self.KEYCLOAK_ADMIN_PASSWORD or "").strip()
        if not kc_pass or kc_pass.lower() == "admin" or len(kc_pass) < 12:
            errors.append(
                "KEYCLOAK_ADMIN_PASSWORD must be rotated in production (non-default, >= 12 chars)."
            )

        db_url = (self.DATABASE_URL or "").strip()
        if (
            "eng_app_pass_change_in_prod" in db_url
            or "manta_password" in db_url
            or _is_localhost_url(db_url)
        ):
            errors.append(
                "DATABASE_URL must use rotated production credentials and a non-localhost host."
            )

        origins = self.cors_origins
        if not origins:
            errors.append("CORS_ORIGINS must be explicitly configured in production.")
        for orig in origins:
            if orig == "*" or not orig.startswith("https://") or _is_localhost_url(orig):
                errors.append(
                    f"CORS_ORIGINS entry must be an explicit HTTPS non-localhost origin: {orig!r}"
                )

        issuer = (self.OIDC_ISSUER or "").strip()
        if not issuer or not issuer.startswith("https://") or _is_localhost_url(issuer):
            errors.append("OIDC_ISSUER must be explicitly set to a public HTTPS URL in production.")

        jwks_url = (self.OIDC_JWKS_URL or "").strip()
        if (
            not jwks_url
            or not jwks_url.startswith(("https://", "http://"))
            or _is_localhost_url(jwks_url)
        ):
            errors.append(
                "OIDC_JWKS_URL must be explicitly set to a non-localhost internal or HTTPS URL in production."
            )

        web_base = (self.WEB_BASE_URL or "").strip()
        if not web_base.startswith("https://") or _is_localhost_url(web_base):
            errors.append("WEB_BASE_URL must be an explicit HTTPS non-localhost URL in production.")

        webhook_base = (self.WEBHOOK_BASE_URL or "").strip()
        if (
            not webhook_base
            or not webhook_base.startswith("https://")
            or _is_localhost_url(webhook_base)
        ):
            errors.append(
                "WEBHOOK_BASE_URL must be explicitly set to an HTTPS non-localhost URL in production."
            )

        seed_env_truthy = self.SEED_DEV_DATA is True or os.getenv(
            "SEED_DEV_DATA", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if seed_env_truthy:
            errors.append("SEED_DEV_DATA must be false in production.")

        return errors

    def assert_production_security(self) -> None:
        """Fail fast at startup if any production hardening invariant is violated."""
        errors = self.validate_production_security()
        if errors:
            raise RuntimeError(
                "Production security configuration check failed:\n- " + "\n- ".join(errors)
            )

    @property
    def jwks_url(self) -> str:
        return self.OIDC_JWKS_URL or (
            f"{self.KEYCLOAK_URL.rstrip('/')}/realms/{self.KEYCLOAK_REALM}"
            "/protocol/openid-connect/certs"
        )

    @property
    def issuer(self) -> str:
        return self.OIDC_ISSUER or f"{self.KEYCLOAK_URL.rstrip('/')}/realms/{self.KEYCLOAK_REALM}"

    @property
    def allowed_issuers(self) -> set[str]:
        if self.OIDC_ISSUER:
            issuers = {self.OIDC_ISSUER}
        else:
            primary = f"{self.KEYCLOAK_URL.rstrip('/')}/realms/{self.KEYCLOAK_REALM}"
            issuers = {
                primary,
                f"http://localhost:8080/realms/{self.KEYCLOAK_REALM}",
                f"http://keycloak:8080/realms/{self.KEYCLOAK_REALM}",
            }
        if self.OIDC_EXTRA_ISSUERS:
            for extra in self.OIDC_EXTRA_ISSUERS.split(","):
                if extra.strip():
                    issuers.add(extra.strip())
        return issuers


settings = Settings()
