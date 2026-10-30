"""Automated validation of M.A.N.T.A. Role-Permission Matrix (SCRUM-306 / US-11).

Asserts JSON schema validity, 100% matrix coverage (explicit ALLOW/DENY for every pair),
parity with Architecture Blueprint §6.2 baseline, and security gate invariants.
"""

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MATRIX_FILE = REPO_ROOT / "docs" / "roles-permissions.json"

EXPECTED_ROLES = {
    "DEVELOPER",
    "SCRUM_MASTER",
    "PROJECT_MANAGER",
    "SOLUTION_ARCHITECT",
    "DELIVERY_DIRECTOR",
    "TOP_MANAGEMENT",
    "TENANT_ADMIN",
}

EXPECTED_ACTIONS = {
    "VIEW_PROJECT_DORA",
    "VIEW_PORTFOLIO_OVERVIEW",
    "VIEW_SPRINT_BURNDOWN_WIP",
    "RECEIVE_SCOPE_CREEP_ALERT",
    "REASSIGN_PR_REVIEWER_MOBILE",
    "VIEW_OWN_CODE_QUALITY",
    "VIEW_TECH_DEBT_AND_SCIP_GRAPH",
    "TRIGGER_REFACTOR_ANALYSIS",
    "SEARCH_ADR",
    "APPROVE_ADR_TENANT_PRIVATE",
    "PUBLISH_GLOBAL_ADR",
    "MANAGE_WEBHOOK_INTEGRATIONS",
    "MANAGE_TENANT_USERS_AND_ROLES",
}

# Verbatim Architecture Blueprint §6.2 matrix (using canonical DELIVERY_DIRECTOR for DELIVERY_EXEC)
ARCH_6_2_BASELINE = {
    "VIEW_PROJECT_DORA": {
        "DEVELOPER": "ALLOW",
        "SCRUM_MASTER": "ALLOW",
        "SOLUTION_ARCHITECT": "ALLOW",
        "DELIVERY_DIRECTOR": "ALLOW",
        "TENANT_ADMIN": "ALLOW",
    },
    "TRIGGER_REFACTOR_ANALYSIS": {
        "DEVELOPER": "ALLOW",
        "SCRUM_MASTER": "DENY",
        "SOLUTION_ARCHITECT": "ALLOW",
        "DELIVERY_DIRECTOR": "DENY",
        "TENANT_ADMIN": "ALLOW",
    },
    "APPROVE_ADR_TENANT_PRIVATE": {
        "DEVELOPER": "DENY",
        "SCRUM_MASTER": "DENY",
        "SOLUTION_ARCHITECT": "ALLOW",
        "DELIVERY_DIRECTOR": "DENY",
        "TENANT_ADMIN": "ALLOW",
    },
    "PUBLISH_GLOBAL_ADR": {
        "DEVELOPER": "DENY",
        "SCRUM_MASTER": "DENY",
        "SOLUTION_ARCHITECT": "ALLOW",
        "DELIVERY_DIRECTOR": "DENY",
        "TENANT_ADMIN": "ALLOW",
    },
    "REASSIGN_PR_REVIEWER_MOBILE": {
        "DEVELOPER": "DENY",
        "SCRUM_MASTER": "ALLOW",
        "SOLUTION_ARCHITECT": "DENY",
        "DELIVERY_DIRECTOR": "ALLOW",
        "TENANT_ADMIN": "ALLOW",
    },
    "MANAGE_WEBHOOK_INTEGRATIONS": {
        "DEVELOPER": "DENY",
        "SCRUM_MASTER": "DENY",
        "SOLUTION_ARCHITECT": "DENY",
        "DELIVERY_DIRECTOR": "DENY",
        "TENANT_ADMIN": "ALLOW",
    },
}


class TestRolePermissionsMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert MATRIX_FILE.is_file(), f"Matrix file missing at {MATRIX_FILE}"
        with open(MATRIX_FILE, "r", encoding="utf-8") as f:
            cls.data = json.load(f)

    def test_json_structure_and_metadata(self):
        """Verify root-level metadata and structure of roles-permissions.json."""
        self.assertEqual(self.data.get("version"), "1.0.0")
        self.assertIn("M.A.N.T.A.", self.data.get("system", ""))
        self.assertIn("SCRUM-247", self.data.get("parent_story", ""))
        self.assertIn("SCRUM-306", self.data.get("task", ""))
        self.assertTrue(len(self.data.get("sources", [])) >= 2)
        self.assertIn("tenant_isolation", self.data.get("scope_rules", {}))
        self.assertIn("project_scoping", self.data.get("scope_rules", {}))
        self.assertIn("contributor_data_scoping", self.data.get("scope_rules", {}))

    def test_seven_canonical_roles_declared(self):
        """Verify all seven canonical roles from SRS §2.3 are declared without duplicates."""
        roles = self.data.get("roles", [])
        role_ids = [r.get("id") for r in roles]

        # No duplicate role IDs
        self.assertEqual(len(role_ids), len(set(role_ids)), "Duplicate role IDs found")
        # Exactly the 7 expected roles
        self.assertEqual(set(role_ids), EXPECTED_ROLES)

        # Check DELIVERY_DIRECTOR has DELIVERY_EXEC alias
        dd_role = next(r for r in roles if r.get("id") == "DELIVERY_DIRECTOR")
        self.assertIn("DELIVERY_EXEC", dd_role.get("aliases", []))

        # Check all required fields on every role
        for role in roles:
            self.assertTrue(role.get("display_name"), f"Missing display_name on {role}")
            self.assertTrue(role.get("description"), f"Missing description on {role}")
            self.assertTrue(role.get("source"), f"Missing source on {role}")

    def test_thirteen_actions_declared(self):
        """Verify all thirteen platform actions across 5 domains are defined."""
        actions = self.data.get("actions", [])
        action_ids = [a.get("id") for a in actions]

        # No duplicate action IDs
        self.assertEqual(len(action_ids), len(set(action_ids)), "Duplicate action IDs found")
        # Exactly the 13 expected actions
        self.assertEqual(set(action_ids), EXPECTED_ACTIONS)

        # Check required fields on every action
        for action in actions:
            self.assertTrue(action.get("domain"), f"Missing domain on {action}")
            self.assertTrue(action.get("description"), f"Missing description on {action}")
            self.assertTrue(len(action.get("preconditions", [])) > 0, f"Missing preconditions on {action}")
            self.assertTrue(action.get("source"), f"Missing source on {action}")

    def test_full_matrix_coverage_explicit_decisions(self):
        """
        SCRUM-306 Acceptance Criteria:
        1. Every role/action pair has an explicit decision (ALLOW or DENY).
        2. Total decided pairs = 7 roles * 13 actions = 91 explicit cells.
        """
        matrix = self.data.get("matrix", {})
        self.assertEqual(set(matrix.keys()), EXPECTED_ROLES, "Matrix must key on exactly the 7 roles")

        total_decisions = 0
        for role_id in EXPECTED_ROLES:
            role_actions = matrix.get(role_id, {})
            self.assertEqual(
                set(role_actions.keys()),
                EXPECTED_ACTIONS,
                f"Role {role_id} is missing decisions for actions: {EXPECTED_ACTIONS - set(role_actions.keys())}",
            )
            for action_id, cell in role_actions.items():
                decision = cell.get("decision")
                self.assertIn(
                    decision,
                    ("ALLOW", "DENY"),
                    f"Cell [{role_id}][{action_id}] must have explicit 'ALLOW' or 'DENY', got '{decision}'",
                )
                self.assertIn(
                    cell.get("source"),
                    ("ARCHITECTURE_6.2", "SRS_DERIVED"),
                    f"Cell [{role_id}][{action_id}] must state authoritative source",
                )
                self.assertIsInstance(
                    cell.get("needs_ba_review"),
                    bool,
                    f"Cell [{role_id}][{action_id}] must specify boolean needs_ba_review",
                )
                self.assertTrue(cell.get("notes"), f"Cell [{role_id}][{action_id}] must contain rationale notes")
                total_decisions += 1

        self.assertEqual(total_decisions, 7 * 13, f"Expected 91 explicit decisions, counted {total_decisions}")

    def test_architecture_6_2_baseline_parity(self):
        """
        Inviolable Invariant:
        Verify the 6 operations from Architecture Blueprint §6.2 across the 5 Architecture roles
        match §6.2 decisions verbatim (with DELIVERY_EXEC resolving to DELIVERY_DIRECTOR).
        """
        matrix = self.data.get("matrix", {})
        for action_id, expected_decisions in ARCH_6_2_BASELINE.items():
            for role_id, expected_decision in expected_decisions.items():
                actual_cell = matrix[role_id][action_id]
                self.assertEqual(
                    actual_cell.get("decision"),
                    expected_decision,
                    f"Baseline mismatch on §6.2 [{role_id}][{action_id}]: expected {expected_decision}, got {actual_cell.get('decision')}",
                )
                self.assertEqual(
                    actual_cell.get("source"),
                    "ARCHITECTURE_6.2",
                    f"Baseline cell [{role_id}][{action_id}] must be sourced from ARCHITECTURE_6.2",
                )
                self.assertFalse(
                    actual_cell.get("needs_ba_review"),
                    f"Baseline cell [{role_id}][{action_id}] is already architecturally approved and must not require BA review",
                )

    def test_security_gate_publish_global_adr(self):
        """
        Architecture §6.5 Security Invariant:
        Only SOLUTION_ARCHITECT and TENANT_ADMIN can publish global ADRs.
        Regular developers and other roles are strictly DENY.
        """
        matrix = self.data.get("matrix", {})
        allowed_roles = {
            role_id
            for role_id in EXPECTED_ROLES
            if matrix[role_id]["PUBLISH_GLOBAL_ADR"]["decision"] == "ALLOW"
        }
        self.assertEqual(
            allowed_roles,
            {"SOLUTION_ARCHITECT", "TENANT_ADMIN"},
            f"PUBLISH_GLOBAL_ADR must be strictly restricted to SOLUTION_ARCHITECT and TENANT_ADMIN, found: {allowed_roles}",
        )

    def test_security_gate_manage_webhook_integrations(self):
        """
        Architecture §6.2 Security Invariant:
        Only TENANT_ADMIN can manage third-party webhook integrations.
        All other 6 roles are strictly DENY.
        """
        matrix = self.data.get("matrix", {})
        allowed_roles = {
            role_id
            for role_id in EXPECTED_ROLES
            if matrix[role_id]["MANAGE_WEBHOOK_INTEGRATIONS"]["decision"] == "ALLOW"
        }
        self.assertEqual(
            allowed_roles,
            {"TENANT_ADMIN"},
            f"MANAGE_WEBHOOK_INTEGRATIONS must be strictly restricted to TENANT_ADMIN, found: {allowed_roles}",
        )

    def test_top_management_read_only_governance(self):
        """
        SRS §2.3 / Alignment Invariant:
        TOP_MANAGEMENT has high-level strategic read-only oversight.
        Operational actions (reassign reviewers, trigger refactor, approve ADRs, webhook config) must be DENY.
        """
        tm_permissions = self.data["matrix"]["TOP_MANAGEMENT"]
        operational_actions = [
            "REASSIGN_PR_REVIEWER_MOBILE",
            "TRIGGER_REFACTOR_ANALYSIS",
            "APPROVE_ADR_TENANT_PRIVATE",
            "PUBLISH_GLOBAL_ADR",
            "MANAGE_WEBHOOK_INTEGRATIONS",
            "MANAGE_TENANT_USERS_AND_ROLES",
        ]
        for action in operational_actions:
            self.assertEqual(
                tm_permissions[action]["decision"],
                "DENY",
                f"TOP_MANAGEMENT must not possess operational permission {action}",
            )


if __name__ == "__main__":
    unittest.main()
