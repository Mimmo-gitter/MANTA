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

    @property
    def jwks_url(self) -> str:
        return self.OIDC_JWKS_URL or (
            f"{self.KEYCLOAK_URL.rstrip('/')}/realms/{self.KEYCLOAK_REALM}"
            "/protocol/openid-connect/certs"
        )


settings = Settings()