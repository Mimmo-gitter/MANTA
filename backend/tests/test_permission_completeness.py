"""Automated completeness test for M.A.N.T.A. Permission Matrix and Route Permissions (SCRUM-310 / US-11).

Parent User Story: SCRUM-247 (US-11 Role-based access control)

Objective:
Fail the build if a role/action pair is missing from the matrix or a route has no permission.

Acceptance Criteria:
1. Adding a route without a permission, or an action without a decision, fails the test.
2. The test runs in CI.
"""

import copy
import json
from pathlib import Path
import unittest

from fastapi import APIRouter
import yaml

from app.auth.middleware import JWTAuthMiddleware
from app.auth.permissions import (
    Permission,
    PermissionChecker,
    assert_permission_matrix_complete,
    assert_registered_routes_complete,
    extract_all_app_routes,
    get_route_declared_permissions,
    require_permission,
    validate_permission_matrix_completeness,
    validate_registered_routes_completeness,
)
from app.main import app
from scripts.verify_permission_completeness import run_checks

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_MATRIX_PATH = REPO_ROOT / "docs" / "roles-permissions.json"
BACKEND_MATRIX_PATH = REPO_ROOT / "backend" / "app" / "auth" / "roles-permissions.json"
WEB_MATRIX_PATH = REPO_ROOT / "web" / "src" / "config" / "roles-permissions.json"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


class TestPermissionMatrixCompleteness(unittest.TestCase):
    """
    Scope 1: Test that every role x action cell has an explicit decision (ALLOW or DENY).
    AC 1: An action without a decision fails the test.
    """

    @classmethod
    def setUpClass(cls) -> None:
        assert DOCS_MATRIX_PATH.is_file(), f"Matrix file missing at {DOCS_MATRIX_PATH}"
        with open(DOCS_MATRIX_PATH, "r", encoding="utf-8") as f:
            cls.docs_matrix_data = json.load(f)

    def test_current_matrix_completeness_baseline(self) -> None:
        """
        Baseline completeness verification:
        Every role x action cell must have an explicit ALLOW or DENY decision across all 7 roles x 13 actions (91 cells).
        """
        errors = validate_permission_matrix_completeness(self.docs_matrix_data)
        self.assertEqual(
            errors,
            [],
            f"Production matrix in docs/roles-permissions.json has completeness violations: {errors}",
        )
        # Verify assert_permission_matrix_complete does not raise
        assert_permission_matrix_complete(self.docs_matrix_data)

        # Count total evaluated explicit cells
        roles = self.docs_matrix_data.get("roles", [])
        actions = self.docs_matrix_data.get("actions", [])
        matrix = self.docs_matrix_data.get("matrix", {})

        self.assertEqual(len(roles), 7, f"Expected 7 roles, found {len(roles)}")
        self.assertEqual(len(actions), 13, f"Expected 13 actions, found {len(actions)}")

        total_cells = 0
        for r in roles:
            role_id = r["id"]
            self.assertIn(role_id, matrix)
            for a in actions:
                action_id = a["id"]
                self.assertIn(action_id, matrix[role_id])
                decision = matrix[role_id][action_id].get("decision")
                self.assertIn(
                    decision,
                    ("ALLOW", "DENY"),
                    f"Cell [{role_id}][{action_id}] must have explicit 'ALLOW' or 'DENY', got '{decision}'",
                )
                total_cells += 1

        self.assertEqual(
            total_cells,
            91,
            f"Expected exactly 91 explicit decisions (7 roles x 13 actions), counted {total_cells}",
        )

    def test_adding_action_without_decision_fails_completeness_check(self) -> None:
        """
        Acceptance Criteria 1:
        Adding a new action without defining explicit decisions across the role matrix MUST fail the build.
        """
        tampered = copy.deepcopy(self.docs_matrix_data)
        # Add new action to declared actions list without adding cells in matrix
        new_action_id = "EXPORT_AUDIT_LOG_ARCHIVE"
        tampered["actions"].append(
            {
                "id": new_action_id,
                "domain": "SECURITY_AUDIT",
                "description": "Export tenant audit log archives",
                "preconditions": ["Tenant active"],
                "source": "PROPOSED_ADDITION",
            }
        )

        errors = validate_permission_matrix_completeness(tampered)
        self.assertTrue(
            len(errors) > 0,
            "Completeness validation must fail when an action is added without decisions",
        )
        # Every role should have a reported missing decision for this action
        for role in tampered["roles"]:
            role_id = role["id"]
            expected_msg = (
                f"Missing decision cell for role '{role_id}' and action '{new_action_id}' in matrix"
            )
            self.assertTrue(
                any(expected_msg in e for e in errors),
                f"Missing expected error for role '{role_id}' on action '{new_action_id}'",
            )

        with self.assertRaises(AssertionError) as ctx:
            assert_permission_matrix_complete(tampered)
        self.assertIn("Permission matrix completeness check failed", str(ctx.exception))
        self.assertIn(new_action_id, str(ctx.exception))

    def test_cell_missing_decision_property_fails_completeness_check(self) -> None:
        """A cell present in matrix without 'decision' key MUST fail the completeness check."""
        tampered = copy.deepcopy(self.docs_matrix_data)
        del tampered["matrix"]["DEVELOPER"]["VIEW_PROJECT_DORA"]["decision"]

        errors = validate_permission_matrix_completeness(tampered)
        self.assertTrue(
            any(
                "Cell [DEVELOPER][VIEW_PROJECT_DORA] has invalid or missing decision" in e
                for e in errors
            ),
            f"Expected error for missing decision key, got {errors}",
        )

        with self.assertRaises(AssertionError):
            assert_permission_matrix_complete(tampered)

    def test_cell_with_null_or_empty_decision_fails_completeness_check(self) -> None:
        """A cell with None, empty string, or non-ALLOW/DENY decision MUST fail the completeness check."""
        for invalid_val in (None, "", "MAYBE", "UNKNOWN", 123):
            tampered = copy.deepcopy(self.docs_matrix_data)
            tampered["matrix"]["TENANT_ADMIN"]["MANAGE_WEBHOOK_INTEGRATIONS"]["decision"] = (
                invalid_val
            )

            errors = validate_permission_matrix_completeness(tampered)
            self.assertTrue(
                any(
                    "Cell [TENANT_ADMIN][MANAGE_WEBHOOK_INTEGRATIONS] has invalid or missing decision"
                    in e
                    for e in errors
                ),
                f"Expected violation for invalid decision '{invalid_val}', got: {errors}",
            )
            with self.assertRaises(AssertionError):
                assert_permission_matrix_complete(tampered)

    def test_adding_role_without_matrix_row_fails_completeness_check(self) -> None:
        """Adding a new role without adding its row in matrix MUST fail the completeness check."""
        tampered = copy.deepcopy(self.docs_matrix_data)
        new_role_id = "SECURITY_AUDITOR"
        tampered["roles"].append(
            {
                "id": new_role_id,
                "display_name": "Security Auditor",
                "description": "Read-only audit inspector",
                "source": "PROPOSED_ADDITION",
            }
        )

        errors = validate_permission_matrix_completeness(tampered)
        self.assertTrue(
            any(
                f"Role '{new_role_id}' is defined in 'roles' but missing from 'matrix'" in e
                for e in errors
            ),
            f"Expected missing role error for '{new_role_id}', got: {errors}",
        )
        with self.assertRaises(AssertionError):
            assert_permission_matrix_complete(tampered)

    def test_matrix_parity_across_repository(self) -> None:
        """
        Verify that docs/roles-permissions.json, backend/app/auth/roles-permissions.json,
        and web/src/config/roles-permissions.json all exist, pass completeness, and are identical.
        """
        paths = [DOCS_MATRIX_PATH, BACKEND_MATRIX_PATH, WEB_MATRIX_PATH]
        loaded = []
        for path in paths:
            self.assertTrue(path.is_file(), f"Matrix file missing at {path}")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert_permission_matrix_complete(data)
            loaded.append((path, data))

        # Check all three have identical data
        base_path, base_data = loaded[0]
        for other_path, other_data in loaded[1:]:
            self.assertEqual(
                base_data,
                other_data,
                f"Matrix in {other_path} has drifted from {base_path}",
            )


class TestRegisteredRoutesCompleteness(unittest.TestCase):
    """
    Scope 2: Test that every registered API route has a declared permission.
    AC 1: Adding a route without a permission fails the test.
    """

    def setUp(self) -> None:
        self._original_routes = list(app.routes)

    def tearDown(self) -> None:
        app.routes[:] = self._original_routes

    def test_current_registered_routes_completeness_baseline(self) -> None:
        """
        Baseline completeness verification:
        Every registered API route that is not public MUST have a declared, valid matrix permission.
        """
        errors = validate_registered_routes_completeness(app)
        self.assertEqual(
            errors,
            [],
            f"Current registered routes have permission completeness violations: {errors}",
        )
        assert_registered_routes_complete(app)

    def test_adding_route_without_permission_fails_completeness_check(self) -> None:
        """
        Acceptance Criteria 1:
        Adding a route without a permission MUST fail the completeness test.
        """
        test_router = APIRouter(prefix="/api/v1/test-unprotected-endpoint")

        @test_router.get("/data")
        async def unprotected_handler():
            return {"sensitive": "leak"}

        app.include_router(test_router)

        errors = validate_registered_routes_completeness(app)
        expected_offender = "GET /api/v1/test-unprotected-endpoint/data"
        self.assertTrue(
            any(expected_offender in err and "no declared permission" in err for err in errors),
            f"Expected error for '{expected_offender}', got {errors}",
        )

        with self.assertRaises(AssertionError) as ctx:
            assert_registered_routes_complete(app)
        self.assertIn("Route permission completeness check failed", str(ctx.exception))
        self.assertIn(expected_offender, str(ctx.exception))

    def test_adding_route_with_empty_permissions_fails_completeness_check(self) -> None:
        """Adding a route with an empty permission declaration (frozenset()) MUST fail completeness."""
        test_router = APIRouter(prefix="/api/v1/test-empty-permissions")

        @test_router.post("/action")
        async def empty_perm_handler():
            return {"action": "done"}

        # Simulate empty permissions tag
        empty_perm_handler._required_permissions = frozenset()

        app.include_router(test_router)

        errors = validate_registered_routes_completeness(app)
        expected_offender = "POST /api/v1/test-empty-permissions/action"
        self.assertTrue(
            any(expected_offender in err and "no declared permission" in err for err in errors),
            f"Expected error for '{expected_offender}', got {errors}",
        )
        with self.assertRaises(AssertionError):
            assert_registered_routes_complete(app)

    def test_adding_route_with_unknown_permission_fails_completeness_check(self) -> None:
        """Adding a route declaring an unregistered permission action MUST fail completeness."""
        test_router = APIRouter(prefix="/api/v1/test-unknown-permission")

        @test_router.get("/rogue")
        async def rogue_perm_handler():
            return {"rogue": True}

        rogue_action = "TOTALLY_UNREGISTERED_ACTION_XYZ"
        rogue_perm_handler._required_permissions = frozenset({rogue_action})

        app.include_router(test_router)

        errors = validate_registered_routes_completeness(app)
        expected_offender = "GET /api/v1/test-unknown-permission/rogue"
        self.assertTrue(
            any(
                expected_offender in err and rogue_action in err and "unknown permission" in err
                for err in errors
            ),
            f"Expected error for '{expected_offender}' with '{rogue_action}', got {errors}",
        )
        with self.assertRaises(AssertionError):
            assert_registered_routes_complete(app)

    def test_public_routes_are_safely_exempted(self) -> None:
        """Verify that known public endpoints are exempted from permission enforcement."""
        routes = extract_all_app_routes(app)
        public_routes = [(path, r) for path, r in routes if JWTAuthMiddleware.is_public(path)]
        self.assertTrue(
            len(public_routes) >= 5,
            f"Expected at least 5 public routes, found {len(public_routes)}",
        )

        public_paths = {p for p, _ in public_routes}
        self.assertIn("/health", public_paths)
        self.assertIn("/docs", public_paths)
        self.assertIn("/openapi.json", public_paths)
        self.assertIn("/tenants/register", public_paths)

    def test_permission_checker_registers_required_permissions(self) -> None:
        """Verify that PermissionChecker dependency and decorator attach _required_permissions."""
        checker = require_permission(Permission.VIEW_PROJECT_DORA)
        self.assertIsInstance(checker, PermissionChecker)
        self.assertEqual(checker.required_permissions, frozenset({"VIEW_PROJECT_DORA"}))

        # Test as decorator
        @require_permission(Permission.VIEW_PORTFOLIO_OVERVIEW)
        async def dummy_endpoint():
            return {}

        self.assertEqual(
            get_route_declared_permissions(dummy_endpoint),
            frozenset({"VIEW_PORTFOLIO_OVERVIEW"}),
        )


class TestCompletenessCIIntegration(unittest.TestCase):
    """
    Scope: Ensure automated completeness tests run in CI (AC 2).
    """

    def test_completeness_test_runs_in_ci_pipeline(self) -> None:
        """
        Acceptance Criteria 2:
        Verify .github/workflows/ci.yml runs backend pytest suite (which includes this test),
        and that backend-test is required by ci-gate.
        """
        self.assertTrue(
            CI_WORKFLOW_PATH.is_file(), f"CI workflow file not found at {CI_WORKFLOW_PATH}"
        )
        with open(CI_WORKFLOW_PATH, "r", encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        jobs = workflow.get("jobs", {})
        self.assertIn("backend-test", jobs, "backend-test job missing from ci.yml")
        backend_test_job = jobs["backend-test"]

        # Check steps in backend-test
        steps = backend_test_job.get("steps", [])
        pytest_step = next(
            (s for s in steps if "pytest" in s.get("run", "")),
            None,
        )
        self.assertIsNotNone(pytest_step, "backend-test must include a pytest step")
        cmd = pytest_step.get("run", "")
        self.assertIn("pytest tests", cmd, f"Expected 'pytest tests' in run command: {cmd}")
        self.assertIn(
            "--ignore=tests/security", cmd, f"Expected '--ignore=tests/security' in: {cmd}"
        )

        # Check that this file is placed under tests/ and outside tests/security
        this_file = Path(__file__).resolve()
        self.assertTrue(
            "tests" in this_file.parts and "security" not in this_file.parts,
            f"{this_file} must be in backend/tests and not in backend/tests/security",
        )

        # Check that ci-gate requires backend-test
        ci_gate = jobs.get("ci-gate", {})
        needs = ci_gate.get("needs", [])
        self.assertIn("backend-test", needs, "ci-gate must depend on backend-test")

        # Check that failure in backend-test blocks CI gate
        gate_script = "\n".join(s.get("run", "") for s in ci_gate.get("steps", []) if "run" in s)
        self.assertIn("needs.backend-test.result", gate_script)

    def test_cli_runner_execution(self) -> None:
        """Verify the CLI verification runner executes successfully and returns 0 exit code."""
        code = run_checks(target="all", verbose=False)
        self.assertEqual(code, 0, f"verify_permission_completeness.py failed with exit code {code}")


if __name__ == "__main__":
    unittest.main()
