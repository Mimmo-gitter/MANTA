from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = (
        "postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod@localhost:5432/manta_db"
    )
    MIGRATION_DATABASE_URL: Optional[str] = None
    REDIS_URL: str = "redis://localhost:6379"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
