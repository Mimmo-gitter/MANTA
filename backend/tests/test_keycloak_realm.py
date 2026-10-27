import json
import os
import unittest


class TestKeycloakRealmExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
        cls.realm_path = os.path.join(repo_root, "infra", "keycloak", "realm-export.json")

    def _load_realm(self):
        with open(self.realm_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_realm_file_exists(self):
        """Verify the realm-export.json file exists at infra/keycloak/realm-export.json."""
        self.assertTrue(
            os.path.isfile(self.realm_path),
            f"Expected realm file to exist at {self.realm_path}",
        )

    def test_realm_json_structure(self):
        """Verify the realm JSON parses and contains required top-level configuration."""
        data = self._load_realm()
        self.assertEqual(data.get("realm"), "manta")
        self.assertEqual(data.get("id"), "manta")
        self.assertTrue(data.get("enabled"))
        self.assertEqual(data.get("sslRequired"), "none")

    def test_required_roles_present(self):
        """Verify all 5 required roles from Blueprint Section 6.2 are declared."""
        data = self._load_realm()
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

    def test_portal_client_pkce_and_urls(self):
        """Verify manta-portal client is configured with PKCE (S256), redirect URIs and web origins."""
        data = self._load_realm()
        clients = {c.get("clientId"): c for c in data.get("clients", [])}
        self.assertIn("manta-portal", clients)

        portal = clients["manta-portal"]
        self.assertTrue(portal.get("enabled"))
        self.assertTrue(portal.get("publicClient"))
        self.assertTrue(portal.get("standardFlowEnabled"))
        self.assertTrue(portal.get("directAccessGrantsEnabled"))

        # Verify PKCE S256 requirement
        attributes = portal.get("attributes", {})
        self.assertEqual(attributes.get("pkce.code.challenge.method"), "S256")

        # Verify redirect and post-logout URIs
        redirect_uris = portal.get("redirectUris", [])
        self.assertTrue(any("3000" in uri for uri in redirect_uris))
        post_logout = attributes.get("post.logout.redirect.uris", "")
        self.assertIn("3000", post_logout)

        # Verify web origins
        web_origins = portal.get("webOrigins", [])
        self.assertTrue(len(web_origins) > 0)

    def test_tenant_id_claim_mapper_configured(self):
        """Verify tenant_id claim mapper is present on manta-portal client (US-4 Scope 2)."""
        data = self._load_realm()
        clients = {c.get("clientId"): c for c in data.get("clients", [])}
        portal = clients["manta-portal"]
        mappers = portal.get("protocolMappers", [])

        tenant_mapper = next((m for m in mappers if m.get("name") == "tenant_id"), None)
        self.assertIsNotNone(tenant_mapper, "tenant_id claim mapper must be configured on manta-portal")
        self.assertEqual(tenant_mapper.get("protocolMapper"), "oidc-usermodel-attribute-mapper")

        config = tenant_mapper.get("config", {})
        self.assertEqual(config.get("user.attribute"), "tenant_id")
        self.assertEqual(config.get("claim.name"), "tenant_id")
        self.assertEqual(config.get("access.token.claim"), "true")
        self.assertEqual(config.get("id.token.claim"), "true")

    def test_audience_mapper_configured(self):
        """Verify audience mapper is present so issued tokens contain expected audience (US-4 AC 1)."""
        data = self._load_realm()
        clients = {c.get("clientId"): c for c in data.get("clients", [])}
        portal = clients["manta-portal"]
        mappers = portal.get("protocolMappers", [])

        aud_mapper = next(
            (m for m in mappers if m.get("protocolMapper") == "oidc-audience-mapper"), None
        )
        self.assertIsNotNone(aud_mapper, "Audience protocol mapper must be configured on manta-portal")
        config = aud_mapper.get("config", {})
        self.assertEqual(config.get("included.client.audience"), "manta-backend")
        self.assertEqual(config.get("access.token.claim"), "true")

    def test_multi_tenant_users_exist(self):
        """Verify test users from at least two different tenants exist (US-4 AC 2)."""
        data = self._load_realm()
        users = data.get("users", [])

        tenants_found = set()
        for user in users:
            tenant_ids = user.get("attributes", {}).get("tenant_id", [])
            for tid in tenant_ids:
                if tid:
                    tenants_found.add(tid)

        self.assertGreaterEqual(
            len(tenants_found),
            2,
            f"Expected users from at least 2 distinct tenants, but found {len(tenants_found)}: {tenants_found}",
        )

    def test_multi_tenant_user_roles(self):
        """Verify realistic users across tenants have valid platform roles and credentials."""
        data = self._load_realm()
        users = {u.get("username"): u for u in data.get("users", [])}

        required_test_users = ["alpha_admin", "alpha_dev", "beta_admin", "beta_dev"]
        for username in required_test_users:
            self.assertIn(username, users, f"Expected user {username} to exist")
            user = users[username]
            self.assertTrue(user.get("enabled"))
            credentials = user.get("credentials", [])
            self.assertTrue(len(credentials) > 0)
            self.assertEqual(credentials[0].get("type"), "password")
            self.assertTrue(len(user.get("realmRoles", [])) > 0)


if __name__ == "__main__":
    unittest.main()
