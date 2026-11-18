"""Negative test verification runner for Row-Level Security (RLS) policies (SCRUM-305).

Parent User Story: SCRUM-246 (US-10 Tenant boundary security tests)

Objective:
Prove that the safety net actually catches a regression by deliberately disabling
an RLS policy (or NO FORCE) in a throwaway database and verifying that the
security test stage fails. Then restore the policy and verify that the security
test stage passes.

Modes supported:
- disable-rls: Executes ALTER TABLE <table> DISABLE ROW LEVEL SECURITY
- no-force:    Executes ALTER TABLE <table> NO FORCE ROW LEVEL SECURITY
- drop-policy: Executes DROP POLICY tenant_isolation_policy ON <table>
- all:         Sequentially verifies all three regression scenarios
"""

import argparse
import asyncio
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Optional, Sequence
from urllib.parse import urlparse, urlunparse

BACKEND_DIR = Path(__file__).resolve().parent.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import pool, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402

DEFAULT_THROWAWAY_DB = "manta_rls_negative_test"
DEFAULT_TARGET_TABLE = "projects"
DEFAULT_TEST_TARGETS = [
    "tests/security",
]
FAST_RESTORE_TARGETS = [
    "tests/security/test_00_rls_guardrails_security.py",
]


class RLSNegativeCheckError(RuntimeError):
    """Raised when the negative RLS safety net verification fails."""

    def __init__(self, message: str, returncode: int = 1, output: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.returncode = returncode
        self.output = output


def get_base_admin_db_url(database_name: Optional[str] = None) -> str:
    """Derive the administrative PostgreSQL connection URL.

    If database_name is provided, replaces the path component with database_name.
    """
    url = getattr(settings, "MIGRATION_DATABASE_URL", None) or os.getenv("MIGRATION_DATABASE_URL")
    if not url:
        base_url = settings.DATABASE_URL
        if "eng_app_user" in base_url:
            admin_user = os.getenv("POSTGRES_USER", "manta_user")
            admin_pass = os.getenv("POSTGRES_PASSWORD", "manta_password")
            url = re.sub(r"://[^@]+@", f"://{admin_user}:{admin_pass}@", base_url)
        else:
            url = base_url

    if database_name:
        parsed = urlparse(url)
        url = urlunparse(parsed._replace(path=f"/{database_name}"))

    return url


def get_app_db_url(database_name: str) -> str:
    """Derive the runtime application connection URL (eng_app_user) for a given database."""
    base_url = settings.DATABASE_URL
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


async def wait_for_postgres(max_retries: int = 30, delay_seconds: float = 1.0) -> None:
    """Wait until PostgreSQL accepts TCP connections and authenticates."""
    engine = create_async_engine(get_base_admin_db_url(), poolclass=pool.NullPool)
    last_error: Optional[Exception] = None
    try:
        for attempt in range(1, max_retries + 1):
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                print(
                    f"==> [Wait] PostgreSQL is ready and accepting connections (attempt {attempt})."
                )
                return
            except Exception as e:
                last_error = e
                await asyncio.sleep(delay_seconds)
        raise RLSNegativeCheckError(
            f"PostgreSQL was not ready after {max_retries} attempts: {last_error}"
        )
    finally:
        await engine.dispose()


async def execute_admin_sql(
    target_db: str, sql_commands: list[str], autocommit: bool = False
) -> None:
    """Execute raw SQL statements as admin on the specified database."""
    admin_url = get_base_admin_db_url(target_db)
    kwargs = {"poolclass": pool.NullPool}
    if autocommit:
        kwargs["isolation_level"] = "AUTOCOMMIT"

    engine = create_async_engine(admin_url, **kwargs)
    try:
        if autocommit:
            async with engine.connect() as conn:
                for statement in sql_commands:
                    await conn.execute(text(statement))
        else:
            async with engine.begin() as conn:
                for statement in sql_commands:
                    await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def create_throwaway_database(db_name: str, maintenance_db: str = "manta_db") -> None:
    """Create a pristine throwaway database, terminating any existing connections first."""
    print(f"==> [Setup] Creating throwaway database '{db_name}'...")
    statements = [
        f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE);",
        f"CREATE DATABASE {db_name} OWNER manta_user;",
    ]
    await execute_admin_sql(maintenance_db, statements, autocommit=True)
    print(f"==> [Setup] Throwaway database '{db_name}' created successfully.")


async def drop_throwaway_database(db_name: str, maintenance_db: str = "manta_db") -> None:
    """Drop the throwaway database cleanly."""
    print(f"==> [Cleanup] Dropping throwaway database '{db_name}'...")
    statements = [
        f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE);",
    ]
    await execute_admin_sql(maintenance_db, statements, autocommit=True)
    print(f"==> [Cleanup] Throwaway database '{db_name}' dropped successfully.")


def apply_migrations_to_database(db_name: str) -> None:
    """Run alembic upgrade head against the specified database."""
    print(f"==> [Migrations] Applying migrations to '{db_name}'...")
    env = dict(os.environ)
    env["MIGRATION_DATABASE_URL"] = get_base_admin_db_url(db_name)
    env["DATABASE_URL"] = get_app_db_url(db_name)

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RLSNegativeCheckError(
            f"Failed to apply migrations to throwaway database '{db_name}':\n{result.stderr}\n{result.stdout}",
            returncode=result.returncode,
            output=result.stdout + "\n" + result.stderr,
        )
    print(f"==> [Migrations] Schema migrated to head on '{db_name}'.")


def run_security_test_stage(
    db_name: str,
    test_targets: Optional[list[str]] = None,
    extra_env: Optional[dict[str, str]] = None,
    failfast: bool = False,
) -> tuple[int, str]:
    """Execute pytest security tests against the throwaway database.

    Returns:
        (returncode, combined_output)
    """
    targets = test_targets or DEFAULT_TEST_TARGETS
    env = dict(os.environ)
    env["MIGRATION_DATABASE_URL"] = get_base_admin_db_url(db_name)
    env["DATABASE_URL"] = get_app_db_url(db_name)
    if extra_env:
        env.update(extra_env)

    cmd = [sys.executable, "-m", "pytest"] + targets + ["-v"]
    if failfast:
        cmd.append("-x")
    result = subprocess.run(
        cmd,
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    combined = result.stdout + "\n" + result.stderr
    return result.returncode, combined


async def tamper_rls(db_name: str, table: str, mode: str) -> None:
    """Apply the deliberate security regression to the throwaway database."""
    print(
        f"==> [Tamper] Applying negative security regression: mode='{mode}' on table='{table}'..."
    )
    if mode == "disable-rls":
        sql = [f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;"]
    elif mode == "no-force":
        sql = [f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;"]
    elif mode == "drop-policy":
        sql = [f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table};"]
    else:
        raise ValueError(f"Unknown tampering mode '{mode}'")

    await execute_admin_sql(db_name, sql)
    print(f"==> [Tamper] Regression applied: mode='{mode}' on table='{table}'.")


async def restore_rls(db_name: str, table: str, mode: str) -> None:
    """Restore the security policy and settings to pristine state."""
    print(f"==> [Restore] Restoring security policies: mode='{mode}' on table='{table}'...")
    sql = [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;",
    ]
    if mode in ("drop-policy", "all"):
        col = "id" if table == "tenants" else "tenant_id"
        sql.extend(
            [
                f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table};",
                f"""
                CREATE POLICY tenant_isolation_policy ON {table}
                    AS PERMISSIVE FOR ALL
                    TO eng_app_user
                    USING ({col} = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
                    WITH CHECK ({col} = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
                """,
            ]
        )

    await execute_admin_sql(db_name, sql)
    print(f"==> [Restore] Restored RLS and FORCE RLS on table '{table}'.")


async def run_single_negative_cycle(
    db_name: str,
    table: str,
    mode: str,
    test_targets: Optional[list[str]] = None,
    verbose: bool = False,
    run_baseline: bool = True,
    restore_targets: Optional[list[str]] = None,
) -> None:
    """Execute one negative test cycle:

    1. Baseline check (must PASS, when run_baseline=True)
    2. Tamper check (must FAIL)
    3. Restore check (must PASS)
    """
    print("\n" + "=" * 70)
    print(f"STARTING NEGATIVE RLS VERIFICATION: mode='{mode}', table='{table}'")
    print("=" * 70)

    # 1. Baseline verification
    if run_baseline:
        print("\n[Phase 1] Running baseline security tests with policies intact...")
        code, output = run_security_test_stage(db_name, test_targets)
        if code != 0:
            if verbose:
                print(output)
            raise RLSNegativeCheckError(
                f"Baseline security tests failed prior to negative tampering (code={code})!\n{output}",
                returncode=code,
                output=output,
            )
        print("[Phase 1: PASS] Baseline security tests passed with all policies active.")
    else:
        print("\n[Phase 1] Skipping redundant baseline check (already verified).")

    # 2. Tampering - deliberate regression
    print(f"\n[Phase 2] Inducing security regression (mode='{mode}' on '{table}')...")
    await tamper_rls(db_name, table, mode)

    # 3. Security tests MUST fail
    print("[Phase 2] Running security tests (EXPECTING FAILURE)...")
    fail_code, fail_output = run_security_test_stage(db_name, test_targets, failfast=True)
    if fail_code == 0:
        raise RLSNegativeCheckError(
            f"[FATAL SAFETY NET FAILURE] Security tests PASSED despite '{mode}' on '{table}'!\n"
            f"The test suite failed to detect that Row-Level Security was compromised.",
            returncode=1,
            output=fail_output,
        )
    print(
        f"[Phase 2: SUCCESS] Safety net verified! Security stage failed as expected (exit code {fail_code})."
    )
    if verbose:
        for line in fail_output.splitlines():
            if "FAILED" in line or "AssertionError" in line:
                print(f"    Caught expected failure: {line.strip()}")

    # 4. Restoration
    print(f"\n[Phase 3] Restoring policies on '{table}'...")
    await restore_rls(db_name, table, mode)

    # 5. Security tests MUST pass again
    effective_restore_targets = restore_targets if restore_targets is not None else test_targets
    print("[Phase 3] Running security tests after restoration (EXPECTING SUCCESS)...")
    restored_code, restored_output = run_security_test_stage(db_name, effective_restore_targets)
    if restored_code != 0:
        if verbose:
            print(restored_output)
        raise RLSNegativeCheckError(
            f"Security tests failed after restoring policies for mode '{mode}' on '{table}' (code={restored_code})!\n{restored_output}",
            returncode=restored_code,
            output=restored_output,
        )
    print("[Phase 3: PASS] Security tests passed after policies were restored.")
    print(
        f"\n>>> [VERIFIED] Negative test scenario '{mode}' on table '{table}' passed completely.\n"
    )


async def run_negative_rls_check(
    db_name: str = DEFAULT_THROWAWAY_DB,
    table: str = DEFAULT_TARGET_TABLE,
    mode: str = "disable-rls",
    keep_db: bool = False,
    test_targets: Optional[list[str]] = None,
    verbose: bool = False,
    skip_baseline: bool = False,
) -> int:
    """Full end-to-end negative verification workflow across throwaway DB lifecycle."""
    maintenance_db = "manta_db"
    modes_to_test = ["disable-rls", "no-force", "drop-policy"] if mode == "all" else [mode]

    try:
        await create_throwaway_database(db_name, maintenance_db=maintenance_db)
        apply_migrations_to_database(db_name)

        uses_default_targets = test_targets is None or list(test_targets) == list(
            DEFAULT_TEST_TARGETS
        )
        for idx, m in enumerate(modes_to_test):
            is_last_mode = idx == len(modes_to_test) - 1
            effective_restore_targets = (
                FAST_RESTORE_TARGETS
                if (not is_last_mode and uses_default_targets)
                else test_targets
            )
            await run_single_negative_cycle(
                db_name=db_name,
                table=table,
                mode=m,
                test_targets=test_targets,
                verbose=verbose,
                run_baseline=(idx == 0 and not skip_baseline),
                restore_targets=effective_restore_targets,
            )

        print("\n" + "=" * 70)
        print("ALL NEGATIVE RLS SECURITY VERIFICATION CHECKS PASSED SUCCESSFULLY!")
        print("Safety net proven: Disabling RLS/FORCE causes security tests to fail;")
        print("restoring RLS/FORCE causes security tests to pass.")
        print("=" * 70 + "\n")
        return 0
    finally:
        if not keep_db:
            try:
                await drop_throwaway_database(db_name, maintenance_db=maintenance_db)
            except Exception as e:
                print(
                    f"Warning: Failed to drop throwaway database '{db_name}': {e}", file=sys.stderr
                )
        else:
            print(f"[Notice] Preserved throwaway database '{db_name}' (--keep-db requested).")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that the pipeline fails when an RLS policy or FORCE RLS is disabled (SCRUM-305)."
    )
    parser.add_argument(
        "--step",
        choices=[
            "wait",
            "create-db",
            "baseline",
            "tamper",
            "expect-failure",
            "restore",
            "expect-success",
            "drop-db",
            "all",
        ],
        default="all",
        help="Execute an individual verification step or 'all' for full automated workflow (default: all)",
    )
    parser.add_argument(
        "--mode",
        choices=["disable-rls", "no-force", "drop-policy", "all"],
        default="all",
        help="Negative regression mode to execute (default: all)",
    )
    parser.add_argument(
        "--table",
        default=DEFAULT_TARGET_TABLE,
        help=f"Target tenant table to tamper with (default: {DEFAULT_TARGET_TABLE})",
    )
    parser.add_argument(
        "--db-name",
        default=DEFAULT_THROWAWAY_DB,
        help=f"Throwaway database name (default: {DEFAULT_THROWAWAY_DB})",
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="Do not drop the throwaway database on completion (useful for debugging)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose test failure logs and details",
    )
    parser.add_argument(
        "--test-target",
        action="append",
        dest="test_targets",
        help="Specific pytest target(s) to execute as security stage (repeatable)",
    )
    parser.add_argument(
        "--skip-baseline",
        action="store_true",
        help="Skip initial baseline pytest run (useful in CI when security-test already verified baseline)",
    )
    return parser.parse_args(argv)


async def execute_step(args: argparse.Namespace) -> int:
    step = args.step
    db_name = args.db_name
    table = args.table
    mode = args.mode if args.mode != "all" else "disable-rls"
    targets = args.test_targets if args.test_targets else DEFAULT_TEST_TARGETS

    if step == "wait":
        await wait_for_postgres()
        return 0
    elif step == "create-db":
        await create_throwaway_database(db_name)
        apply_migrations_to_database(db_name)
        return 0
    elif step == "baseline":
        code, out = run_security_test_stage(db_name, targets)
        if code != 0:
            raise RLSNegativeCheckError(f"Baseline test failed: {out}", returncode=code, output=out)
        print("==> [baseline] Passed: Security tests passed with policies intact.")
        return 0
    elif step == "tamper":
        await tamper_rls(db_name, table, mode)
        return 0
    elif step == "expect-failure":
        code, out = run_security_test_stage(db_name, targets, failfast=True)
        if code == 0:
            raise RLSNegativeCheckError(
                f"[FATAL] Security tests passed despite tampering ('{mode}' on '{table}')!",
                returncode=1,
                output=out,
            )
        print(f"==> [expect-failure] Passed: Security tests failed as expected (exit code {code}).")
        return 0
    elif step == "restore":
        await restore_rls(db_name, table, mode)
        return 0
    elif step == "expect-success":
        code, out = run_security_test_stage(db_name, targets)
        if code != 0:
            raise RLSNegativeCheckError(
                f"Security tests failed after restoration: {out}", returncode=code, output=out
            )
        print("==> [expect-success] Passed: Security tests passed after policies restored.")
        return 0
    elif step == "drop-db":
        await drop_throwaway_database(db_name)
        return 0
    elif step == "all":
        return await run_negative_rls_check(
            db_name=db_name,
            table=table,
            mode=args.mode,
            keep_db=args.keep_db,
            test_targets=targets,
            verbose=args.verbose,
            skip_baseline=args.skip_baseline,
        )
    else:
        raise ValueError(f"Unknown step '{step}'")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        exit_code = asyncio.run(execute_step(args))
        return exit_code
    except RLSNegativeCheckError as e:
        print(f"\n[FATAL ERROR] {e.message}", file=sys.stderr)
        return e.returncode
    except Exception as e:
        print(f"\n[UNEXPECTED ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
