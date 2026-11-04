"""SCRUM-246 / US-10 (SCRUM-304 & SCRUM-305): CI & CLI Negative RLS Safety Net Unit Tests.

Validates the negative RLS safety net runner CLI, URL generation, error handling,
and GitHub Actions CI workflow invariants:
1. CI workflow defines dedicated 'security-test' stage (executes 'pytest tests/security -v').
2. CI workflow defines 'security-negative-test' stage (executes 'verify_rls_negative.py --mode all -v').
3. CI gate requires both stages to succeed before allowing PR merge.
4. 'backend-test' ignores 'tests/security' to prevent duplicate execution.
5. CLI argument parsing and connection URL construction in verify_rls_negative.py.
"""

from pathlib import Path
import unittest
from unittest.mock import patch
import yaml

from scripts import verify_rls_negative

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


class TestRLSNegativeCIWorkflow(unittest.TestCase):
    """Verify GitHub Actions CI workflow configuration and branch protection gate."""

    @classmethod
    def setUpClass(cls) -> None:
        assert CI_WORKFLOW_PATH.exists(), f"CI workflow file not found at {CI_WORKFLOW_PATH}"
        cls.workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.jobs = cls.workflow.get("jobs", {})

    def test_ci_workflow_defines_security_test_job(self) -> None:
        """Verify .github/workflows/ci.yml defines dedicated security-test stage."""
        self.assertIn("security-test", self.jobs, "Job 'security-test' missing from ci.yml")
        sec_job = self.jobs["security-test"]
        self.assertEqual(sec_job.get("runs-on"), "ubuntu-latest")

        services = sec_job.get("services", {})
        self.assertIn("postgres", services)
        self.assertEqual(services["postgres"].get("image"), "pgvector/pgvector:pg16")

        steps = sec_job.get("steps", [])
        step_commands = [s.get("run", "") for s in steps if "run" in s]
        self.assertTrue(any("verify_migrations.py --step wait" in cmd for cmd in step_commands))
        self.assertTrue(any("verify_migrations.py --step upgrade" in cmd for cmd in step_commands))
        self.assertTrue(any("pytest tests/security" in cmd for cmd in step_commands))

        # Check ci-gate dependency
        ci_gate = self.jobs.get("ci-gate", {})
        self.assertIn("security-test", ci_gate.get("needs", []))
        gate_script = "\n".join(s.get("run", "") for s in ci_gate.get("steps", []) if "run" in s)
        self.assertIn("needs.security-test.result", gate_script)

    def test_ci_workflow_defines_security_negative_test_job(self) -> None:
        """Verify .github/workflows/ci.yml defines dedicated security-negative-test job."""
        self.assertIn(
            "security-negative-test", self.jobs, "Job 'security-negative-test' missing from ci.yml"
        )
        neg_job = self.jobs["security-negative-test"]
        self.assertEqual(neg_job.get("runs-on"), "ubuntu-latest")

        services = neg_job.get("services", {})
        self.assertIn("postgres", services)
        self.assertEqual(services["postgres"].get("image"), "pgvector/pgvector:pg16")

        steps = neg_job.get("steps", [])
        step_commands = [s.get("run", "") for s in steps if "run" in s]
        self.assertTrue(
            any("scripts/verify_rls_negative.py" in cmd for cmd in step_commands),
            "Step executing verify_rls_negative.py missing from security-negative-test",
        )

        # Check ci-gate dependency
        ci_gate = self.jobs.get("ci-gate", {})
        self.assertIn("security-negative-test", ci_gate.get("needs", []))
        gate_script = "\n".join(s.get("run", "") for s in ci_gate.get("steps", []) if "run" in s)
        self.assertIn("needs.security-negative-test.result", gate_script)

    def test_backend_test_ignores_security_directory(self) -> None:
        """Verify backend-test excludes tests/security to avoid redundant execution."""
        backend_job = self.jobs.get("backend-test", {})
        backend_steps = backend_job.get("steps", [])
        backend_commands = [s.get("run", "") for s in backend_steps if "run" in s]
        self.assertTrue(
            any("--ignore=tests/security" in cmd for cmd in backend_commands),
            "backend-test must include --ignore=tests/security",
        )


class TestRLSNegativeCLIRunner(unittest.TestCase):
    """Unit tests for verify_rls_negative.py CLI arguments, URL derivation, and error types."""

    def test_cli_default_arguments(self) -> None:
        """Verify default CLI parameters."""
        args = verify_rls_negative.parse_args([])
        self.assertEqual(args.step, "all")
        self.assertEqual(args.mode, "all")
        self.assertEqual(args.table, "projects")
        self.assertEqual(args.db_name, "manta_rls_negative_test")
        self.assertFalse(args.keep_db)
        self.assertFalse(args.verbose)
        self.assertIsNone(args.test_targets)
        self.assertEqual(verify_rls_negative.DEFAULT_TEST_TARGETS, ["tests/security"])

    def test_cli_custom_arguments(self) -> None:
        """Verify CLI parser with custom arguments and repeatable flags."""
        argv = [
            "--step",
            "tamper",
            "--mode",
            "no-force",
            "--table",
            "audit_logs",
            "--db-name",
            "custom_neg_db",
            "--keep-db",
            "-v",
            "--test-target",
            "tests/security/test_00_rls_guardrails_security.py",
        ]
        args = verify_rls_negative.parse_args(argv)
        self.assertEqual(args.step, "tamper")
        self.assertEqual(args.mode, "no-force")
        self.assertEqual(args.table, "audit_logs")
        self.assertEqual(args.db_name, "custom_neg_db")
        self.assertTrue(args.keep_db)
        self.assertTrue(args.verbose)
        self.assertEqual(args.test_targets, ["tests/security/test_00_rls_guardrails_security.py"])

    def test_get_base_admin_db_url_derivation(self) -> None:
        """Verify admin database URL derived properly with target db name."""
        with patch.object(
            verify_rls_negative.settings,
            "MIGRATION_DATABASE_URL",
            "postgresql+asyncpg://manta_user:manta_pass@localhost:5432/manta_db",
        ):
            url = verify_rls_negative.get_base_admin_db_url("manta_throwaway")
            self.assertIn("manta_throwaway", url)
            self.assertIn("manta_user", url)

    def test_get_app_db_url_derivation(self) -> None:
        """Verify app runtime database URL derived with target db name."""
        with patch.object(
            verify_rls_negative.settings,
            "DATABASE_URL",
            "postgresql+asyncpg://eng_app_user:app_pass@localhost:5432/manta_db",
        ):
            url = verify_rls_negative.get_app_db_url("manta_throwaway")
            self.assertIn("manta_throwaway", url)
            self.assertIn("eng_app_user", url)

    def test_rls_negative_check_error_attributes(self) -> None:
        """Verify RLSNegativeCheckError stores message, returncode, and output."""
        err = verify_rls_negative.RLSNegativeCheckError(
            message="Test failed", returncode=2, output="Sample failure log"
        )
        self.assertEqual(err.message, "Test failed")
        self.assertEqual(err.returncode, 2)
        self.assertEqual(err.output, "Sample failure log")
        self.assertEqual(str(err), "Test failed")


if __name__ == "__main__":
    unittest.main()
