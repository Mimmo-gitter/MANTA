"""
TASK-301-5 (SCRUM-500 / STORY-301): Production Database Migration Runner.

Executes `alembic upgrade head` strictly WITHOUT running local dev seed scripts
(`scripts.seed_dev_db`), enforces `SEED_DEV_DATA=false`, and rotates the
least-privilege PostgreSQL `eng_app_user` password to match the configured
production runtime secret (`ENG_APP_DB_PASSWORD` or `DATABASE_URL`).

Usage:
    SEED_DEV_DATA=false python -m scripts.run_prod_migrations
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlparse

from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
_DEV_DEFAULT_APP_PASSWORD = "eng_app_pass_change_in_prod"


def assert_seed_dev_disabled(env: Mapping[str, str] | None = None) -> None:
    """Fail closed if SEED_DEV_DATA is enabled during production migrations."""
    source = os.environ if env is None else env
    raw = str(source.get("SEED_DEV_DATA", "false")).strip().lower()
    if raw in _TRUTHY_VALUES:
        raise RuntimeError(
            "Refusing to run production migrations with SEED_DEV_DATA enabled. "
            "Set SEED_DEV_DATA=false in production."
        )


def resolve_eng_app_password(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the rotated production password for PostgreSQL role `eng_app_user`."""
    source = os.environ if env is None else env

    explicit = (source.get("ENG_APP_DB_PASSWORD") or "").strip()
    if explicit:
        password = explicit
    else:
        db_url = (source.get("DATABASE_URL") or "").strip()
        if not db_url:
            return None
        parsed = urlparse(db_url)
        username = unquote(parsed.username or "")
        if username != "eng_app_user" or parsed.password is None:
            return None
        password = unquote(parsed.password)

    if not password or "\x00" in password:
        raise ValueError("Invalid eng_app_user password provided.")
    if password == _DEV_DEFAULT_APP_PASSWORD:
        raise ValueError(
            "Production eng_app_user password must be rotated away from "
            "'eng_app_pass_change_in_prod'."
        )
    return password


def run_alembic_upgrade_head(env: Mapping[str, str] | None = None) -> None:
    """Run `alembic upgrade head` in the backend directory without any dev seeding."""
    child_env = dict(os.environ if env is None else env)
    child_env["SEED_DEV_DATA"] = "false"

    cmd = [sys.executable, "-m", "alembic", "upgrade", "head"]
    print("[run_prod_migrations] Executing: alembic upgrade head (SEED_DEV_DATA=false)", flush=True)
    subprocess.run(cmd, cwd=str(BACKEND_DIR), env=child_env, check=True)


async def sync_eng_app_user_password(migration_db_url: str, app_password: str) -> None:
    """Rotate `eng_app_user` SCRAM-SHA-256 password in PostgreSQL using bound SQL params."""
    if not migration_db_url.strip():
        raise ValueError("MIGRATION_DATABASE_URL is required to sync eng_app_user password.")

    engine = create_async_engine(migration_db_url, poolclass=pool.NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('manta.target_app_password', :pwd, true)"),
                {"pwd": app_password},
            )
            await conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'eng_app_user') THEN
                            EXECUTE format(
                                'ALTER ROLE eng_app_user WITH LOGIN PASSWORD %L',
                                current_setting('manta.target_app_password', false)
                            );
                        END IF;
                    END
                    $$;
                    """
                )
            )
            await conn.execute(text("SELECT set_config('manta.target_app_password', '', true)"))
    finally:
        await engine.dispose()
    print(
        "[run_prod_migrations] Synced rotated password for PostgreSQL role eng_app_user.",
        flush=True,
    )


def run_production_migrations(env: Mapping[str, str] | None = None) -> int:
    """Full production migration entrypoint: guard -> alembic upgrade head -> role password sync."""
    source = dict(os.environ if env is None else env)
    assert_seed_dev_disabled(source)

    migration_db_url = (source.get("MIGRATION_DATABASE_URL") or "").strip()
    if not migration_db_url:
        raise RuntimeError("MIGRATION_DATABASE_URL must be set for production migrations.")

    app_password = resolve_eng_app_password(source)
    run_alembic_upgrade_head(source)

    if app_password is not None:
        asyncio.run(sync_eng_app_user_password(migration_db_url, app_password))

    print(
        "[run_prod_migrations] Production migrations completed cleanly (0 dev rows seeded).",
        flush=True,
    )
    return 0


def main() -> int:
    return run_production_migrations()


if __name__ == "__main__":
    sys.exit(main())
