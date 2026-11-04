import asyncio
import logging
from logging.config import fileConfig
import os
import re
import sys
import traceback
from typing import Optional

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import settings
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_STEP_LOG_RE = re.compile(
    r"^Running\s+(upgrade|downgrade)\s+([^\s,]*)\s*->\s*([^\s,]*)(?:,\s*(.*))?$"
)
_VERSION_FILE_RE = re.compile(r"migrations[/\\]versions[/\\]([a-f0-9]+)_[^/\\]+\.py$")


class _RevisionStepTracker(logging.Handler):
    """Tracks the currently executing Alembic revision step from runtime logs."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.direction: Optional[str] = None
        self.from_rev: Optional[str] = None
        self.to_rev: Optional[str] = None
        self.revision_id: Optional[str] = None
        self.description: Optional[str] = None

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage().strip()
        match = _STEP_LOG_RE.match(msg)
        if not match:
            return
        direction, from_rev, to_rev, description = match.groups()
        self.direction = direction
        self.from_rev = from_rev or "base"
        self.to_rev = to_rev or "base"
        self.revision_id = to_rev if direction == "upgrade" else from_rev
        self.description = (description or "").strip()


def _extract_revision_from_traceback(exc: BaseException) -> Optional[str]:
    """Fallback to extract revision ID from traceback frames in migrations/versions/."""
    if exc.__traceback__ is None:
        return None
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        match = _VERSION_FILE_RE.search(frame.filename)
        if match:
            return match.group(1)
    return None


def _run_migrations_with_tracking() -> None:
    """Run Alembic migrations while tracking and printing the failing revision on any error."""
    migration_logger = logging.getLogger("alembic.runtime.migration")
    tracker = _RevisionStepTracker()
    migration_logger.addHandler(tracker)
    try:
        with context.begin_transaction():
            context.run_migrations()
    except Exception as exc:
        failing_rev = tracker.revision_id or _extract_revision_from_traceback(exc) or "unknown"
        direction = tracker.direction or "migration"
        desc_suffix = f" ({tracker.description})" if tracker.description else ""
        transition = ""
        if tracker.from_rev and tracker.to_rev:
            transition = f" [{tracker.from_rev} -> {tracker.to_rev}]"
        prefix = f"ERROR [alembic.env] Migration failed during {direction}"
        detail = f"at revision: {failing_rev}{desc_suffix}{transition}"
        error_msg = f"{prefix} {detail} — {type(exc).__name__}: {exc}"
        print(f"\n{error_msg}", file=sys.stderr, flush=True)
        if os.getenv("GITHUB_ACTIONS") == "true":
            gh_err = f"::error title=Migration Failure (revision {failing_rev})::{error_msg}"
            print(gh_err, file=sys.stderr, flush=True)
        raise
    finally:
        migration_logger.removeHandler(tracker)


def get_migration_url() -> str:
    url = getattr(settings, "MIGRATION_DATABASE_URL", None) or os.getenv("MIGRATION_DATABASE_URL")
    if url:
        return url
    base_url = settings.DATABASE_URL
    if "eng_app_user" in base_url:
        admin_user = os.getenv("POSTGRES_USER", "manta_user")
        admin_pass = os.getenv("POSTGRES_PASSWORD", "manta_password")
        return re.sub(r"://[^@]+@", f"://{admin_user}:{admin_pass}@", base_url)
    return base_url


def run_migrations_offline() -> None:
    url = get_migration_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    _run_migrations_with_tracking()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    _run_migrations_with_tracking()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_migration_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
