from pathlib import Path
import unittest
import yaml


class TestCIProtection(unittest.TestCase):
    def test_ci_branch_protection_gate(self):
        """
        Deliberate test to verify GitHub Actions CI gate and branch protection.

        - DELIBERATE_FAILURE = True:  Pipeline fails (RED)   -> PR merge is BLOCKED.
        - DELIBERATE_FAILURE = False: Pipeline passes (GREEN) -> PR merge is ALLOWED.
        """
        DELIBERATE_FAILURE = False

        self.assertFalse(
            DELIBERATE_FAILURE,
            "Deliberate test failure! Verify that GitHub Actions turns RED "
            "and GitHub blocks merging into develop.",
        )


class TestCIWorkflowOptimizations(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[2]
        cls.ci_data = yaml.safe_load(
            (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        cls.mobile_data = yaml.safe_load(
            (root / ".github" / "workflows" / "mobile-ci.yml").read_text(encoding="utf-8")
        )

    def test_triggers_avoid_duplicate_feature_branch_runs(self) -> None:
        on_cfg = self.ci_data.get("on", self.ci_data.get(True, {}))
        self.assertEqual(on_cfg["push"]["branches"], ["main", "develop"])
        self.assertIn("pull_request", on_cfg)
        self.assertNotIn("branches", on_cfg["pull_request"])
        self.assertIn("schedule", on_cfg)
        self.assertIn("workflow_dispatch", on_cfg)

    def test_path_filter_and_ci_gate_include_all_jobs(self) -> None:
        jobs = self.ci_data["jobs"]
        self.assertIn("changes", jobs)
        expected_needs = {
            "changes",
            "backend-lint",
            "backend-test",
            "security-test",
            "backend-migrations",
            "security-negative-test",
            "pgbouncer-integration",
            "backend-docker-build",
            "web-checks",
            "web-e2e",
            "web-e2e-keycloak",
        }
        self.assertEqual(set(jobs["ci-gate"]["needs"]), expected_needs)
        gate_script = "\n".join(step.get("run", "") for step in jobs["ci-gate"]["steps"])
        for job_id in expected_needs:
            self.assertIn(f"needs.{job_id}.result", gate_script)

    def test_webhook_suites_not_duplicated_in_backend_test(self) -> None:
        jobs = self.ci_data["jobs"]
        backend_pytest = " ".join(
            step.get("run", "")
            for step in jobs["backend-test"]["steps"]
            if "pytest" in step.get("run", "")
        )
        pgbouncer_pytest = " ".join(
            step.get("run", "")
            for step in jobs["pgbouncer-integration"]["steps"]
            if "pytest" in step.get("run", "")
        )
        for webhook_suite in (
            "test_webhook_authentication.py",
            "test_webhook_load.py",
            "test_webhook_staging_and_outage.py",
            "test_webhook_delivery_idempotency.py",
        ):
            self.assertIn(f"--ignore=tests/{webhook_suite}", backend_pytest)
            self.assertIn(f"backend/tests/{webhook_suite}", pgbouncer_pytest)

    def test_security_negative_skips_redundant_baseline_and_upgrade(self) -> None:
        steps = self.ci_data["jobs"]["security-negative-test"]["steps"]
        runs = "\n".join(step.get("run", "") for step in steps)
        self.assertNotIn("verify_migrations.py --step upgrade", runs)
        self.assertIn("verify_rls_negative.py --mode all --skip-baseline", runs)

    def test_web_e2e_split_and_build_artifact_sharing(self) -> None:
        jobs = self.ci_data["jobs"]
        upload_steps = [
            step
            for step in jobs["web-checks"]["steps"]
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        self.assertEqual(len(upload_steps), 1)
        self.assertTrue(upload_steps[0]["with"].get("include-hidden-files"))

        self.assertEqual(jobs["web-e2e"]["needs"], ["changes", "web-checks"])
        self.assertEqual(jobs["web-e2e-keycloak"]["needs"], ["changes", "web-checks"])

        mock_env = " ".join(
            f"{k}={v}" for step in jobs["web-e2e"]["steps"] for k, v in step.get("env", {}).items()
        )
        self.assertIn("FORCE_KEYCLOAK_MOCK=true", mock_env)
        keycloak_runs = "\n".join(step.get("run", "") for step in jobs["web-e2e-keycloak"]["steps"])
        self.assertIn("auth-keycloak-journey.spec.ts", keycloak_runs)
        self.assertIn("--workers=1", keycloak_runs)

    def test_mobile_ci_combines_gradle_tasks(self) -> None:
        steps = self.mobile_data["jobs"]["mobile-ci"]["steps"]
        gradle_steps = [step for step in steps if "./gradlew" in step.get("run", "")]
        self.assertEqual(len(gradle_steps), 1)
        cmd = gradle_steps[0]["run"]
        for task in (
            "ktlintCheck",
            "lintDevDebug",
            "lintStageDebug",
            "testDevDebugUnitTest",
            "testStageDebugUnitTest",
            "assembleDebug",
        ):
            self.assertIn(task, cmd)


if __name__ == "__main__":
    unittest.main()
