"""SCRUM-246 / US-10 (SCRUM-305): Negative RLS Safety Net Test Suite.

Proves that the security test safety net actually catches a regression:
1. When an RLS policy is disabled (ALTER TABLE ... DISABLE ROW LEVEL SECURITY),
   the security test stage fails.
2. When FORCE ROW LEVEL SECURITY is disabled (ALTER TABLE ... NO FORCE ROW LEVEL SECURITY),
   the security test stage fails.
3. When the tenant_isolation_policy is dropped (DROP POLICY ...),
   the security test stage fails.
4. When policies and FORCE flags are restored, the security test stage passes.
5. Verifies the CI workflow definition in .github/workflows/ci.yml enforces the
   security-negative-test job and requires it in ci-gate.
"""

from pathlib import Path
import uuid
import pytest
import yaml

from scripts import verify_rls_negative

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture
async def throwaway_test_db():
    """Provisions an isolated throwaway database migrated to head for negative testing."""
    db_name = f"manta_neg_py_{uuid.uuid4().hex[:8]}"
    await verify_rls_negative.create_throwaway_database(db_name)
    verify_rls_negative.apply_migrations_to_database(db_name)
    try:
        yield db_name
    finally:
        try:
            await verify_rls_negative.drop_throwaway_database(db_name)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_negative_rls_pipeline_fails_when_rls_disabled_and_passes_when_restored(
    throwaway_test_db: str,
):
    """AC 1 & 2: Disabling RLS fails the security stage; restoring RLS passes.

    Validates that:
    - Phase 1 (Baseline): Security tests pass with all policies active.
    - Phase 2 (Tampered): ALTER TABLE projects DISABLE ROW LEVEL SECURITY causes
      security tests to fail (exit code non-zero).
    - Phase 3 (Restored): ALTER TABLE projects ENABLE ROW LEVEL SECURITY restores
      green security test status (exit code 0).
    """
    await verify_rls_negative.run_single_negative_cycle(
        db_name=throwaway_test_db,
        table="projects",
        mode="disable-rls",
        verbose=True,
    )


@pytest.mark.asyncio
async def test_negative_rls_pipeline_fails_when_no_force_and_passes_when_restored(
    throwaway_test_db: str,
):
    """AC 1 & 2: Disabling FORCE RLS fails the security stage; restoring FORCE passes.

    Validates that:
    - Phase 1 (Baseline): Security tests pass with FORCE RLS active.
    - Phase 2 (Tampered): ALTER TABLE projects NO FORCE ROW LEVEL SECURITY causes
      security guardrails to fail (detecting relforcerowsecurity = false).
    - Phase 3 (Restored): ALTER TABLE projects FORCE ROW LEVEL SECURITY restores
      green security test status.
    """
    await verify_rls_negative.run_single_negative_cycle(
        db_name=throwaway_test_db,
        table="projects",
        mode="no-force",
        verbose=True,
    )


@pytest.mark.asyncio
async def test_negative_rls_pipeline_fails_when_policy_dropped_and_passes_when_restored(
    throwaway_test_db: str,
):
    """AC 1 & 2: Dropping tenant_isolation_policy fails security stage; restoring passes.

    Validates that:
    - Phase 1 (Baseline): Security tests pass.
    - Phase 2 (Tampered): DROP POLICY tenant_isolation_policy ON projects causes
      security tests to fail.
    - Phase 3 (Restored): Recreating tenant_isolation_policy restores green status.
    """
    await verify_rls_negative.run_single_negative_cycle(
        db_name=throwaway_test_db,
        table="projects",
        mode="drop-policy",
        verbose=True,
    )


@pytest.mark.asyncio
async def test_expect_failure_throws_if_security_tests_unexpectedly_pass(
    throwaway_test_db: str,
):
    """Verify that if the security tests do NOT fail when tampered, the check raises an error.

    Guarantees the negative test itself validates the safety net rather than blindly passing.
    """
    # When table is in pristine state (not tampered), running an 'expect-failure' step must raise
    args = verify_rls_negative.parse_args(
        ["--step", "expect-failure", "--db-name", throwaway_test_db]
    )
    with pytest.raises(verify_rls_negative.RLSNegativeCheckError) as exc_info:
        await verify_rls_negative.execute_step(args)

    assert "Security tests passed despite" in exc_info.value.message


def test_ci_workflow_defines_security_negative_test_job():
    """AC 3: Verify .github/workflows/ci.yml defines security-negative-test and gates merging.

    Invariants:
    1. Job `security-negative-test` exists in CI workflow.
    2. Runs on `ubuntu-latest` with PostgreSQL service.
    3. Executes `python scripts/verify_rls_negative.py --mode all`.
    4. Required by `ci-gate` in `needs`.
    5. Evaluated in `ci-gate` step logic.
    """
    assert CI_WORKFLOW_PATH.exists(), f"CI workflow file not found at {CI_WORKFLOW_PATH}"
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))

    jobs = workflow.get("jobs", {})
    assert "security-negative-test" in jobs, (
        "Job 'security-negative-test' is missing from .github/workflows/ci.yml"
    )

    sec_job = jobs["security-negative-test"]
    assert sec_job.get("runs-on") == "ubuntu-latest"

    # Verify PostgreSQL service
    services = sec_job.get("services", {})
    assert "postgres" in services, "PostgreSQL service missing from security-negative-test job"
    assert services["postgres"].get("image") == "pgvector/pgvector:pg16"

    # Verify execution of verification script
    steps = sec_job.get("steps", [])
    step_commands = [s.get("run", "") for s in steps if "run" in s]
    script_step_found = any("scripts/verify_rls_negative.py" in cmd for cmd in step_commands)
    assert script_step_found, (
        "Step executing 'python scripts/verify_rls_negative.py' missing from security-negative-test"
    )

    # Verify ci-gate gating
    ci_gate = jobs.get("ci-gate", {})
    gate_needs = ci_gate.get("needs", [])
    assert "security-negative-test" in gate_needs, (
        "ci-gate must include 'security-negative-test' in its needs dependencies"
    )

    gate_script = "\n".join(s.get("run", "") for s in ci_gate.get("steps", []) if "run" in s)
    assert "needs.security-negative-test.result" in gate_script, (
        "ci-gate evaluation script must inspect needs.security-negative-test.result"
    )
