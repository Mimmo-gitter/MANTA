from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    @property
    def cors_origins(self) -> list[str]:
        return [orig.strip() for orig in self.CORS_ORIGINS.split(",") if orig.strip()]

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
