"""Static checks for the committed PgBouncer configuration (SCRUM-245 / US-9).

These tests run in every CI build without any database: they guard the
tenant-safety invariants of infra/pgbouncer/pgbouncer.ini and its wiring in
backend/docker-compose.yml against accidental regressions.
"""

import configparser
import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INI_PATH = os.path.join(REPO_ROOT, "infra", "pgbouncer", "pgbouncer.ini")
COMPOSE_PATH = os.path.join(REPO_ROOT, "backend", "docker-compose.yml")

# PostgreSQL default max_connections (pgvector/pgvector:pg16 image, not overridden).
POSTGRES_MAX_CONNECTIONS = 100


class TestPgBouncerConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parser = configparser.ConfigParser(comment_prefixes=(";", "#"), interpolation=None)
        with open(INI_PATH, "r", encoding="utf-8") as f:
            cls.raw_ini = f.read()
        parser.read_string(cls.raw_ini)
        cls.ini = parser
        cls.pgb = parser["pgbouncer"]
        with open(COMPOSE_PATH, "r", encoding="utf-8") as f:
            cls.compose = f.read()

    def _int(self, key):
        return int(self.pgb[key])

    # --- Tenant-safety invariants -------------------------------------------

    def test_transaction_pool_mode(self):
        self.assertEqual(self.pgb.get("pool_mode"), "transaction")

    def test_server_reset_query_is_discard_all(self):
        self.assertEqual(self.pgb.get("server_reset_query"), "DISCARD ALL")

    def test_server_reset_query_always_enabled(self):
        """Without this PgBouncer skips server_reset_query in transaction mode."""
        self.assertEqual(self.pgb.get("server_reset_query_always"), "1")

    def test_prepared_statement_tracking_disabled(self):
        """Tracking conflicts with DISCARD ALL after every transaction."""
        self.assertEqual(self.pgb.get("max_prepared_statements"), "0")

    def test_no_trailing_inline_comments(self):
        """PgBouncer treats trailing ';'/'#' as part of the value, not a comment."""
        for section in self.ini.sections():
            for key, value in self.ini[section].items():
                self.assertNotRegex(
                    value, r"[;#]", f"[{section}] {key} contains an inline comment: {value!r}"
                )

    # --- Authentication -----------------------------------------------------

    def test_scram_auth_and_no_committed_credentials(self):
        self.assertEqual(self.pgb.get("auth_type"), "scram-sha-256")
        self.assertEqual(self.pgb.get("auth_file"), "/etc/pgbouncer/userlist.txt")
        self.assertNotRegex(self.raw_ini, re.compile(r"password\s*=", re.IGNORECASE))

    def test_wildcard_database_routes_to_postgres_service(self):
        databases = self.ini["databases"]
        self.assertIn("*", databases)
        self.assertIn("host=postgres", databases["*"])
        self.assertIn("port=5432", databases["*"])

    # --- Sizing -------------------------------------------------------------

    def test_pool_sizing_fits_postgres_connection_budget(self):
        default_pool = self._int("default_pool_size")
        reserve_pool = self._int("reserve_pool_size")
        max_db = self._int("max_db_connections")

        self.assertGreater(default_pool, 0)
        self.assertLessEqual(default_pool + reserve_pool, max_db)
        # Leave headroom on Postgres for Keycloak (direct) and admin sessions.
        self.assertLessEqual(max_db, POSTGRES_MAX_CONNECTIONS // 2)

    def test_client_limit_covers_app_pools(self):
        # SQLAlchemy pool_size (10) + max_overflow (20) per process, several processes.
        self.assertGreaterEqual(self._int("max_client_conn"), 100)
        self.assertEqual(self._int("min_pool_size"), 0)

    def test_fail_fast_wait_timeout(self):
        self.assertLessEqual(self._int("query_wait_timeout"), 60)

    # --- docker-compose wiring ---------------------------------------------

    def test_compose_mounts_committed_ini(self):
        self.assertIn(
            "../infra/pgbouncer/pgbouncer.ini:/etc/pgbouncer/pgbouncer.ini:ro", self.compose
        )

    def test_compose_has_no_conflicting_pool_env_vars(self):
        """The mounted ini is the single source of truth for pooling settings."""
        for env_var in ("POOL_MODE", "MAX_CLIENT_CONN", "DEFAULT_POOL_SIZE"):
            self.assertNotRegex(self.compose, rf"^\s*{env_var}\s*:", re.MULTILINE)

    def test_compose_image_is_pinned(self):
        match = re.search(r"image:\s*edoburu/pgbouncer:(\S+)", self.compose)
        self.assertIsNotNone(match)
        self.assertNotEqual(match.group(1), "latest")


if __name__ == "__main__":
    unittest.main()
