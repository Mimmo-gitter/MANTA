"""CI and local verification runner for Alembic database migrations (SCRUM-263).

Executes and validates:
1. PostgreSQL TCP/auth readiness check (`--step wait`)
2. `alembic upgrade head` (`--step upgrade`)
3. `alembic downgrade base` + zero orphaned tables check (`--step downgrade`)
4. `alembic upgrade head` re-application (`--step reupgrade`)
5. `alembic check` model/schema autogenerate drift detection (`--step check`)
"""

import argparse
import asyncio
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Optional, Sequence

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI_PATH = BACKEND_DIR / "alembic.ini"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import pool, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402

STEP_LOG_RE = re.compile(
    r"Running\s+(upgrade|downgrade)\s+([^\s,]*)\s*->\s*([^\s,]*)(?:,\s*(.*))?$"
)
ENV_ERROR_REV_RE = re.compile(r"Migration failed during \w+ at revision:\s*([^\s(]+)")
TRACEBACK_REV_RE = re.compile(r"migrations[/\\]versions[/\\]([a-f0-9]+)_[^/\\]+\.py")


class MigrationCheckError(RuntimeError):
    """Raised when any migration verification phase fails."""

    def __init__(
        self,
        phase: str,
        message: str,
        failing_revision: Optional[str] = None,
        returncode: int = 1,
        output: str = "",
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.message = message
        self.failing_revision = failing_revision or "unknown"
        self.returncode = returncode
        self.output = output


def extract_failing_revision(output: str) -> str:
    """Extract the failing revision ID from Alembic output, env.py logs, or traceback."""
    env_matches = ENV_ERROR_REV_RE.findall(output)
    if env_matches:
        return env_matches[-1]

    last_step_rev: Optional[str] = None
    for line in output.splitlines():
        match = STEP_LOG_RE.search(line.strip())
        if match:
            direction, from_rev, to_rev, _ = match.groups()
            last_step_rev = to_rev if direction == "upgrade" else from_rev

    if last_step_rev:
        return last_step_rev

    tb_matches = TRACEBACK_REV_RE.findall(output)
    if tb_matches:
        return tb_matches[-1]

    return "unknown"


def get_db_url() -> str:
    """Get the administrative database URL for migration operations and checks."""
    url = getattr(settings, "MIGRATION_DATABASE_URL", None) or os.getenv("MIGRATION_DATABASE_URL")
    if url:
        return url
    base_url = settings.DATABASE_URL
    if "eng_app_user" in base_url:
        admin_user = os.getenv("POSTGRES_USER", "manta_user")
        admin_pass = os.getenv("POSTGRES_PASSWORD", "manta_password")
        return re.sub(r"://[^@]+@", f"://{admin_user}:{admin_pass}@", base_url)
    return base_url


async def wait_for_postgres(max_retries: int = 30, delay_seconds: float = 1.0) -> None:
    """Wait until PostgreSQL accepts TCP connections and authenticates."""
    engine = create_async_engine(get_db_url(), poolclass=pool.NullPool)
    last_error: Optional[Exception] = None
    try:
        for attempt in range(1, max_retries + 1):
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                print(f"[wait] PostgreSQL is ready (attempt {attempt}/{max_retries}).", flush=True)
                return
            except Exception as exc:
                last_error = exc
                err_name = type(exc).__name__
                msg = f"[wait] Waiting for PostgreSQL ({attempt}/{max_retries}): {err_name}: {exc}"
                print(msg, flush=True)
                if attempt < max_retries:
                    await asyncio.sleep(delay_seconds)
    finally:
        await engine.dispose()

    raise MigrationCheckError(
        phase="wait",
        message=f"PostgreSQL did not become ready after {max_retries} attempts: {last_error}",
        failing_revision="none",
    )


async def verify_downgrade_base_clean() -> None:
    """Verify that `downgrade base` left no orphaned application tables or active revisions."""
    engine = create_async_engine(get_db_url(), poolclass=pool.NullPool)
    version_exists_sql = (
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'alembic_version')"
    )
    remaining_tables_sql = (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
        "AND table_name != 'alembic_version' ORDER BY table_name"
    )
    try:
        async with engine.connect() as conn:
            version_table_exists = await conn.scalar(text(version_exists_sql))
            if version_table_exists:
                res = await conn.execute(text("SELECT version_num FROM alembic_version"))
                current_revs = list(res.scalars().all())
                if current_revs:
                    msg = f"Expected alembic_version to be empty at base, found: {current_revs}"
                    raise MigrationCheckError(
                        phase="downgrade base",
                        message=msg,
                        failing_revision=current_revs[0],
                    )

            res_tables = await conn.execute(text(remaining_tables_sql))
            remaining_tables = list(res_tables.scalars().all())
            if remaining_tables:
                msg = f"Incomplete downgrade(): leftover tables in public: {remaining_tables}"
                raise MigrationCheckError(
                    phase="downgrade base",
                    message=msg,
                    failing_revision="base",
                )
            print(
                "[downgrade] Verified public schema is clean (0 leftover application tables).",
                flush=True,
            )
    finally:
        await engine.dispose()


def run_alembic_command(
    phase: str,
    alembic_args: Sequence[str],
    ini_path: Path = ALEMBIC_INI_PATH,
    cwd: Path = BACKEND_DIR,
) -> str:
    """Execute an Alembic CLI command, stream output, and raise MigrationCheckError on failure."""
    cmd = [sys.executable, "-m", "alembic", "-c", str(ini_path), *alembic_args]
    cmd_display = f"alembic {' '.join(alembic_args)}"
    print(f"\n==> [{phase}] Running: {cmd_display}", flush=True)

    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = proc.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)

    if proc.returncode != 0:
        failing_rev = extract_failing_revision(output)
        code = proc.returncode
        summary = f"Step '{phase}' ({cmd_display}) failed (exit {code}). Revision: {failing_rev}"
        sep = "=" * 72
        banner_lines = [
            "",
            sep,
            "MIGRATION CHECK FAILED",
            f"Phase:            {phase}",
            f"Command:          {cmd_display}",
            f"Failing Revision: {failing_rev}",
            sep,
        ]
        print("\n".join(banner_lines), file=sys.stderr, flush=True)
        if os.getenv("GITHUB_ACTIONS") == "true":
            gh_msg = f"::error title=Migration Check Failed ({phase})::{summary}"
            print(gh_msg, file=sys.stderr, flush=True)
        raise MigrationCheckError(
            phase=phase,
            message=summary,
            failing_revision=failing_rev,
            returncode=proc.returncode,
            output=output,
        )

    print(f"==> [{phase}] Passed ({cmd_display}).", flush=True)
    return output


def run_step(step: str) -> None:
    """Execute the requested migration check step or full sequence."""
    if step in ("wait", "all"):
        asyncio.run(wait_for_postgres())

    if step in ("upgrade", "all"):
        run_alembic_command("upgrade head (initial)", ["upgrade", "head"])

    if step in ("downgrade", "all"):
        run_alembic_command("downgrade base", ["downgrade", "base"])
        asyncio.run(verify_downgrade_base_clean())

    if step in ("reupgrade", "all"):
        run_alembic_command("upgrade head (re-apply)", ["upgrade", "head"])

    if step in ("check", "all"):
        run_alembic_command("schema drift check", ["check"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    desc = "Verify Alembic database migrations (upgrade, downgrade, re-upgrade, drift check)."
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument(
        "--step",
        choices=("wait", "upgrade", "downgrade", "reupgrade", "check", "all"),
        default="all",
        help="Which verification step to execute (default: all).",
    )
    args = parser.parse_args(argv)

    try:
        run_step(args.step)
    except MigrationCheckError as exc:
        err_msg = f"\nERROR: {exc.message} (Failing revision: {exc.failing_revision})"
        print(err_msg, file=sys.stderr, flush=True)
        return exc.returncode or 1
    except Exception as exc:
        print(f"\nUNEXPECTED ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
