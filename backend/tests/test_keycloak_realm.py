import json
import os
import unittest


class TestKeycloakRealmExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Resolve repo root relative to tests directory
        current_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
        cls.realm_path = os.path.join(repo_root, "infra", "keycloak", "realm-export.json")

    def test_realm_file_exists(self):
        """Verify the realm-export.json file exists at infra/keycloak/realm-export.json."""
        self.assertTrue(
            os.path.isfile(self.realm_path),
            f"Expected realm file to exist at {self.realm_path}",
        )

    def test_realm_json_structure(self):
        """Verify the realm JSON parses and contains required top-level configuration."""
        with open(self.realm_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("realm"), "manta")
        self.assertEqual(data.get("id"), "manta")
        self.assertTrue(data.get("enabled"))
        self.assertEqual(data.get("sslRequired"), "none")

    def test_required_roles_present(self):
        """Verify all 5 required roles from Blueprint Section 6.2 are declared."""
        with open(self.realm_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        expected_roles = {
            "DEVELOPER",
            "SCRUM_MASTER",
            "SOLUTION_ARCHITECT",
            "DELIVERY_EXEC",
            "TENANT_ADMIN",
        }
        realm_roles = {
            role["name"]
            for role in data.get("roles", {}).get("realm", [])
            if "name" in role
        }
        missing_roles = expected_roles - realm_roles
        self.assertEqual(
            missing_roles,
            set(),
            f"Missing required realm roles: {missing_roles}",
        )

    def test_portal_client_configuration(self):
        """Verify manta-portal client is configured for Next.js frontend authentication."""
        with open(self.realm_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        clients = {c.get("clientId"): c for c in data.get("clients", [])}
        self.assertIn("manta-portal", clients)

        portal = clients["manta-portal"]
        self.assertTrue(portal.get("enabled"))
        self.assertTrue(portal.get("publicClient"))
        self.assertTrue(portal.get("standardFlowEnabled"))
        self.assertTrue(portal.get("directAccessGrantsEnabled"))

        # Verify redirect URIs include local web portal
        redirect_uris = portal.get("redirectUris", [])
        self.assertTrue(any("3000" in uri for uri in redirect_uris))

    def test_seed_users_configuration(self):
        """Verify default test users exist and have non-empty credentials."""
        with open(self.realm_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        users = {u.get("username"): u for u in data.get("users", [])}
        self.assertIn("admin", users)
        self.assertIn("developer", users)
        self.assertIn("architect", users)

        for username, user in users.items():
            self.assertTrue(user.get("enabled"), f"User {username} should be enabled")
            credentials = user.get("credentials", [])
            self.assertTrue(
                len(credentials) > 0,
                f"User {username} should have credentials",
            )
            self.assertEqual(credentials[0].get("type"), "password")


if __name__ == "__main__":
    unittest.main()
