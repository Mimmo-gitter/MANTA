"""
TASK-301-5 (SCRUM-500 / STORY-301): Production Hardening Automated Test Suite.

Verifies:
1. `backend/Dockerfile` configures uvicorn with `--proxy-headers --forwarded-allow-ips=*`.
2. Production Keycloak realm overlay & renderer (`infra/keycloak/realm-prod-overlay.json`,
   `infra/keycloak/render_prod_realm.py`) produce a hardened HTTPS realm (`sslRequired: external`,
   `https://manta.<domain>/*` redirect/post-logout URIs, production lifespans, zero dev users,
   canonical SRS roles, strict PKCE S256) and reject unsafe domains.
3. `backend/docker-compose.prod.yml` switches Keycloak to `start --import-realm` with
   `KC_HOSTNAME`, `KC_PROXY_HEADERS=xforwarded`, `KC_HTTP_ENABLED=true`, separates prod
   migrations (`scripts.run_prod_migrations`, `SEED_DEV_DATA=false`) from `seed_dev_db`,
   and wires fail-fast runtime secrets without weak defaults.
4. `app.config.Settings` disables localhost CORS regex in `APP_ENV=production` and fails
   fast on dev default secrets, localhost URLs, or `SEED_DEV_DATA=true`.
5. `scripts.run_prod_migrations` blocks `SEED_DEV_DATA=true`, runs `alembic upgrade head`
   only (no `seed_dev_db`), and rotates `eng_app_user` password safely.
6. `backend/.env.prod.example` declares all required AC variables with zero dev secrets.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import Settings
from scripts import run_prod_migrations

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
DEV_COMPOSE_PATH = REPO_ROOT / "backend" / "docker-compose.yml"
PROD_COMPOSE_PATH = REPO_ROOT / "backend" / "docker-compose.prod.yml"
ENV_PROD_EXAMPLE_PATH = REPO_ROOT / "backend" / ".env.prod.example"
ROLES_MATRIX_PATH = REPO_ROOT / "docs" / "roles-permissions.json"
RENDER_REALM_SCRIPT = REPO_ROOT / "infra" / "keycloak" / "render_prod_realm.py"
PROD_OVERLAY_PATH = REPO_ROOT / "infra" / "keycloak" / "realm-prod-overlay.json"


def _load_render_prod_realm_module() -> Any:
    spec = importlib.util.spec_from_file_location("render_prod_realm", RENDER_REALM_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ComposeYamlLoader(yaml.SafeLoader):
    """PyYAML SafeLoader supporting Docker Compose v2.24+ `!reset` tags."""


def _construct_reset(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeYamlLoader.add_constructor("!reset", _construct_reset)


def _load_compose_yaml(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeYamlLoader)


def _env_to_dict(env_block: Any) -> dict[str, str]:
    if isinstance(env_block, dict):
        return {str(k): str(v) for k, v in env_block.items()}
    if isinstance(env_block, list):
        out: dict[str, str] = {}
        for item in env_block:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                out[k] = v
        return out
    return {}


def _valid_prod_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "APP_ENV": "production",
        "SEED_DEV_DATA": False,
        "DATABASE_URL": "postgresql+asyncpg://eng_app_user:ProdAppSecret998877@pgbouncer:5432/manta_db",
        "MIGRATION_DATABASE_URL": "postgresql+asyncpg://manta_user:ProdAdminSecret998877@postgres:5432/manta_db",
        "CORS_ORIGINS": "https://manta.example.com",
        "OIDC_ISSUER": "https://auth.manta.example.com/realms/manta",
        "OIDC_JWKS_URL": "http://keycloak:8080/realms/manta/protocol/openid-connect/certs",
        "WEB_BASE_URL": "https://manta.example.com",
        "WEBHOOK_BASE_URL": "https://api.manta.example.com",
        "ENCRYPTION_MASTER_KEY": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "KEYCLOAK_ADMIN_PASSWORD": "RotatedKeycloakAdminPass2026",
    }
    base.update(overrides)
    return Settings(**base)


# ==============================================================================
# 1. Backend Dockerfile Proxy Headers
# ==============================================================================


class TestBackendDockerfileHardening:
    def test_uvicorn_cmd_enables_proxy_headers_and_forwarded_allow_ips(self) -> None:
        dockerfile_text = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
        cmd_lines = [
            line.strip() for line in dockerfile_text.splitlines() if line.strip().startswith("CMD ")
        ]
        assert len(cmd_lines) == 1
        cmd_line = cmd_lines[0]
        assert '"uvicorn"' in cmd_line
        assert '"--proxy-headers"' in cmd_line
        assert '"--forwarded-allow-ips=*"' in cmd_line


# ==============================================================================
# 2. Keycloak Production Realm Overlay & Renderer
# ==============================================================================


class TestKeycloakProductionRealmOverlay:
    def test_overlay_renders_hardened_https_production_realm(self, tmp_path: Path) -> None:
        assert PROD_OVERLAY_PATH.is_file()
        mod = _load_render_prod_realm_module()

        out_path = tmp_path / "realm-export.json"
        rendered = mod.render_prod_realm_file(output_path=out_path, domain="example.com")
        assert out_path.is_file()
        reloaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert reloaded == rendered

        # Core production security settings
        assert rendered["realm"] == "manta"
        assert rendered["enabled"] is True
        assert rendered["sslRequired"] == "external"
        assert rendered["registrationAllowed"] is False
        assert rendered["users"] == []

        # Production lifespans (replacing 60s/120s/600s dev test timeouts)
        assert rendered["accessTokenLifespan"] == 900
        assert rendered["accessTokenLifespanForImplicitFlow"] == 900
        assert rendered["ssoSessionIdleTimeout"] == 1800
        assert rendered["ssoSessionMaxLifespan"] == 36000
        assert rendered["ssoSessionIdleTimeoutRememberMe"] == 1800
        assert rendered["ssoSessionMaxLifespanRememberMe"] == 36000
        assert rendered["clientSessionIdleTimeout"] == 1800
        assert rendered["clientSessionMaxLifespan"] == 36000
        assert rendered["offlineSessionIdleTimeout"] == 2592000
        assert rendered["offlineSessionMaxLifespan"] == 5184000
        assert (
            rendered["accessTokenLifespan"]
            < rendered["ssoSessionIdleTimeout"]
            <= rendered["ssoSessionMaxLifespan"]
        )

        # Canonical SRS roles parity with docs/roles-permissions.json
        matrix_data = json.loads(ROLES_MATRIX_PATH.read_text(encoding="utf-8"))
        expected_roles = {r["id"] for r in matrix_data["roles"]}
        realm_roles = {r["name"] for r in rendered["roles"]["realm"]}
        assert realm_roles - {"offline_access"} == expected_roles
        assert "DELIVERY_EXEC" not in realm_roles

        # Hardened manta-portal client
        clients = {c["clientId"]: c for c in rendered["clients"]}
        portal = clients["manta-portal"]
        assert portal["rootUrl"] == "https://manta.example.com"
        assert portal["redirectUris"] == ["https://manta.example.com/*"]
        assert portal["webOrigins"] == ["https://manta.example.com"]
        assert portal["attributes"]["post.logout.redirect.uris"] == "https://manta.example.com/*"
        assert portal["attributes"]["pkce.code.challenge.method"] == "S256"
        assert portal["directAccessGrantsEnabled"] is True
        mapper_names = {m["name"] for m in portal["protocolMappers"]}
        assert {"tenant_id", "audience-manta-backend"} <= mapper_names

        # Hardened manta-mobile client
        mobile = clients["manta-mobile"]
        assert mobile["redirectUris"] == ["manta://auth/callback"]
        assert mobile["attributes"]["post.logout.redirect.uris"] == "manta://auth/callback"
        assert mobile["attributes"]["pkce.code.challenge.method"] == "S256"
        assert mobile["directAccessGrantsEnabled"] is False
        assert mobile["webOrigins"] == []

        # Declarative User Profile preserved and zero localhost/dev passwords leaked
        serialized = json.dumps(rendered)
        assert "localhost" not in serialized
        assert "127.0.0.1" not in serialized
        assert "admin123" not in serialized
        assert "dev123" not in serialized
        assert "mobile123" not in serialized
        assert "org.keycloak.userprofile.UserProfileProvider" in rendered["components"]

    def test_domain_normalization_and_invalid_domain_rejection(self) -> None:
        mod = _load_render_prod_realm_module()

        assert mod.resolve_portal_origin(domain="example.com") == (
            "example.com",
            "https://manta.example.com",
        )
        assert mod.resolve_portal_origin(domain="manta.example.com") == (
            "manta.example.com",
            "https://manta.example.com",
        )
        assert mod.resolve_portal_origin(portal_origin="https://portal.acme.io/") == (
            "portal.acme.io",
            "https://portal.acme.io",
        )

        for bad_domain in (
            "",
            "localhost",
            "127.0.0.1",
            "manta.local",
            "http://manta.example.com",
            "https://manta.example.com",
            "*.example.com",
            "example.com/path",
            "singlelabel",
        ):
            with pytest.raises(ValueError):
                mod.validate_production_domain(bad_domain)

        with pytest.raises(ValueError):
            mod.resolve_portal_origin(portal_origin="http://manta.example.com")


# ==============================================================================
# 3. Docker Compose Production Overlay & Dev Compose Env Wiring
# ==============================================================================


class TestDockerComposeProductionHardening:
    def test_prod_compose_hardens_keycloak_migrations_and_runtime_secrets(self) -> None:
        prod = _load_compose_yaml(PROD_COMPOSE_PATH)
        services = prod["services"]

        # 1. Keycloak production `start` mode behind Caddy
        kc = services["keycloak"]
        assert kc["command"] == ["start", "--import-realm"]
        assert "start-dev" not in " ".join(kc["command"])
        kc_env = _env_to_dict(kc["environment"])
        assert "KC_HOSTNAME:?" in kc_env["KC_HOSTNAME"]
        assert kc_env["KC_PROXY_HEADERS"] == "xforwarded"
        assert kc_env["KC_HTTP_ENABLED"] == "true"
        assert kc_env["KC_HOSTNAME_STRICT_HTTPS"] == "true"
        assert "KEYCLOAK_ADMIN_PASSWORD:?" in kc_env["KEYCLOAK_ADMIN_PASSWORD"]
        assert "POSTGRES_PASSWORD:?" in kc_env["KC_DB_PASSWORD"]
        assert kc["ports"] == []
        assert "keycloak_rendered_realm:/opt/keycloak/data/import:ro" in kc["volumes"]
        assert (
            kc["depends_on"]["keycloak-realm-init"]["condition"] == "service_completed_successfully"
        )

        # 2. keycloak-realm-init renders overlay
        kc_init = services["keycloak-realm-init"]
        assert "render_prod_realm.py" in " ".join(kc_init["command"])

        # 3. Production migrations separated from seed_dev_db (SEED_DEV_DATA=false)
        db_init = services["db-init"]
        db_init_cmd = " ".join(db_init["command"])
        assert "scripts.run_prod_migrations" in db_init_cmd
        assert "seed_dev_db" not in db_init_cmd
        db_init_env = _env_to_dict(db_init["environment"])
        assert db_init_env["SEED_DEV_DATA"] == "false"
        assert "MIGRATION_DATABASE_URL:?" in db_init_env["MIGRATION_DATABASE_URL"]
        assert "DATABASE_URL:?" in db_init_env["DATABASE_URL"]
        assert "ENG_APP_DB_PASSWORD:?" in db_init_env["ENG_APP_DB_PASSWORD"]

        # 4. FastAPI app production env & required runtime secrets
        app_svc = services["app"]
        app_env = _env_to_dict(app_svc["environment"])
        assert app_env["APP_ENV"] == "production"
        assert app_env["SEED_DEV_DATA"] == "false"
        for required_secret in (
            "CORS_ORIGINS",
            "OIDC_ISSUER",
            "OIDC_JWKS_URL",
            "WEB_BASE_URL",
            "WEBHOOK_BASE_URL",
            "ENCRYPTION_MASTER_KEY",
            "DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "KEYCLOAK_ADMIN_PASSWORD",
        ):
            assert f"{required_secret}:?" in app_env[required_secret], (
                f"Expected fail-fast required syntax for {required_secret}"
            )
        assert app_svc["ports"] == []

        # 5. Worker enforces rotated encryption key and SEED_DEV_DATA=false
        worker_env = _env_to_dict(services["worker"]["environment"])
        assert worker_env["SEED_DEV_DATA"] == "false"
        assert "ENCRYPTION_MASTER_KEY:?" in worker_env["ENCRYPTION_MASTER_KEY"]

        # 6. Dev-only Mailpit disabled & DB/broker ports closed
        assert "dev-only" in services["mailpit"]["profiles"]
        assert services["postgres"]["ports"] == []
        assert services["pgbouncer"]["ports"] == []
        assert services["valkey"]["ports"] == []

    def test_dev_compose_app_forwards_cors_jwks_and_encryption_key(self) -> None:
        dev = yaml.safe_load(DEV_COMPOSE_PATH.read_text(encoding="utf-8"))
        app_env = _env_to_dict(dev["services"]["app"]["environment"])
        for key in ("APP_ENV", "CORS_ORIGINS", "OIDC_JWKS_URL", "ENCRYPTION_MASTER_KEY"):
            assert key in app_env, f"backend/docker-compose.yml app missing {key}"


# ==============================================================================
# 4. Backend CORS Hardening & Runtime Production Secret Validation
# ==============================================================================


class TestBackendProductionSecuritySettings:
    def test_cors_localhost_regex_disabled_in_production_and_enabled_in_dev(self) -> None:
        dev_settings = Settings(_env_file=None, APP_ENV="development")
        assert dev_settings.is_production is False
        assert dev_settings.cors_allow_origin_regex is not None
        assert "localhost" in dev_settings.cors_allow_origin_regex
        assert dev_settings.validate_production_security() == []

        prod_settings = _valid_prod_settings()
        assert prod_settings.is_production is True
        assert prod_settings.cors_allow_origin_regex is None
        assert prod_settings.validate_production_security() == []
        prod_settings.assert_production_security()

    @pytest.mark.parametrize(
        ("override", "expected_fragment"),
        [
            (
                {
                    "ENCRYPTION_MASTER_KEY": (
                        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                    )
                },
                "ENCRYPTION_MASTER_KEY",
            ),
            ({"ENCRYPTION_MASTER_KEY": "not-hex"}, "ENCRYPTION_MASTER_KEY"),
            ({"KEYCLOAK_ADMIN_PASSWORD": "admin"}, "KEYCLOAK_ADMIN_PASSWORD"),
            ({"KEYCLOAK_ADMIN_PASSWORD": "short"}, "KEYCLOAK_ADMIN_PASSWORD"),
            (
                {
                    "DATABASE_URL": (
                        "postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod"
                        "@pgbouncer:5432/manta_db"
                    )
                },
                "DATABASE_URL",
            ),
            ({"CORS_ORIGINS": "*"}, "CORS_ORIGINS"),
            ({"CORS_ORIGINS": "http://localhost:3000"}, "CORS_ORIGINS"),
            ({"CORS_ORIGINS": "http://manta.example.com"}, "CORS_ORIGINS"),
            ({"OIDC_ISSUER": None}, "OIDC_ISSUER"),
            ({"OIDC_ISSUER": "http://localhost:8080/realms/manta"}, "OIDC_ISSUER"),
            ({"OIDC_JWKS_URL": None}, "OIDC_JWKS_URL"),
            (
                {
                    "OIDC_JWKS_URL": "http://localhost:8080/realms/manta/protocol/openid-connect/certs"
                },
                "OIDC_JWKS_URL",
            ),
            ({"WEB_BASE_URL": "http://localhost:3000"}, "WEB_BASE_URL"),
            ({"WEBHOOK_BASE_URL": None}, "WEBHOOK_BASE_URL"),
            ({"WEBHOOK_BASE_URL": "http://api.manta.example.com"}, "WEBHOOK_BASE_URL"),
            ({"SEED_DEV_DATA": True}, "SEED_DEV_DATA"),
        ],
    )
    def test_production_security_validator_rejects_unsafe_config(
        self,
        override: dict[str, Any],
        expected_fragment: str,
    ) -> None:
        cfg = _valid_prod_settings(**override)
        errors = cfg.validate_production_security()
        assert any(expected_fragment in err for err in errors), (
            f"Expected error mentioning {expected_fragment!r}, got {errors!r}"
        )
        with pytest.raises(RuntimeError, match=expected_fragment):
            cfg.assert_production_security()


# ==============================================================================
# 5. Production Migration Runner (Separated from seed_dev_db)
# ==============================================================================


class TestProductionMigrationRunner:
    @pytest.mark.parametrize("bad_val", ["true", "1", "YES", "on"])
    def test_refuses_to_run_when_seed_dev_data_enabled(self, bad_val: str) -> None:
        with pytest.raises(RuntimeError, match="SEED_DEV_DATA"):
            run_prod_migrations.assert_seed_dev_disabled({"SEED_DEV_DATA": bad_val})

    def test_resolve_eng_app_password_explicit_and_url_and_rejects_dev_default(self) -> None:
        assert (
            run_prod_migrations.resolve_eng_app_password(
                {"ENG_APP_DB_PASSWORD": "RotatedPass12345"}
            )
            == "RotatedPass12345"
        )
        assert (
            run_prod_migrations.resolve_eng_app_password(
                {
                    "DATABASE_URL": (
                        "postgresql+asyncpg://eng_app_user:UrlPass%212026@pgbouncer:5432/manta_db"
                    )
                }
            )
            == "UrlPass!2026"
        )
        with pytest.raises(ValueError, match="eng_app_pass_change_in_prod"):
            run_prod_migrations.resolve_eng_app_password(
                {"ENG_APP_DB_PASSWORD": "eng_app_pass_change_in_prod"}
            )

    def test_runs_alembic_upgrade_head_without_seed_dev_db(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        commands_run: list[list[str]] = []
        synced_calls: list[tuple[str, str]] = []

        def _fake_subprocess_run(cmd: list[str], **kwargs: Any) -> None:
            assert kwargs.get("check") is True
            assert kwargs["env"]["SEED_DEV_DATA"] == "false"
            commands_run.append(cmd)

        async def _fake_sync(migration_url: str, app_pass: str) -> None:
            synced_calls.append((migration_url, app_pass))

        monkeypatch.setattr(run_prod_migrations.subprocess, "run", _fake_subprocess_run)
        monkeypatch.setattr(run_prod_migrations, "sync_eng_app_user_password", _fake_sync)

        rc = run_prod_migrations.run_production_migrations(
            {
                "SEED_DEV_DATA": "false",
                "MIGRATION_DATABASE_URL": "postgresql+asyncpg://manta_user:AdminSecret99@postgres:5432/manta_db",
                "ENG_APP_DB_PASSWORD": "AppSecret998877",
            }
        )
        assert rc == 0
        assert len(commands_run) == 1
        assert commands_run[0][-3:] == ["alembic", "upgrade", "head"]
        assert not any("seed_dev_db" in part for part in commands_run[0])
        assert synced_calls == [
            (
                "postgresql+asyncpg://manta_user:AdminSecret99@postgres:5432/manta_db",
                "AppSecret998877",
            )
        ]


# ==============================================================================
# 6. Production `.env.prod.example` Completeness & Zero Dev Defaults
# ==============================================================================


class TestEnvProdExample:
    def test_env_prod_example_declares_required_vars_without_dev_secrets(self) -> None:
        raw = ENV_PROD_EXAMPLE_PATH.read_text(encoding="utf-8")
        kv: dict[str, str] = {}
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            kv[k.strip()] = v.strip()

        required_keys = (
            "APP_ENV",
            "SEED_DEV_DATA",
            "MANTA_DOMAIN",
            "KC_HOSTNAME",
            "KC_PROXY_HEADERS",
            "CORS_ORIGINS",
            "OIDC_ISSUER",
            "OIDC_JWKS_URL",
            "WEB_BASE_URL",
            "WEBHOOK_BASE_URL",
            "ENCRYPTION_MASTER_KEY",
            "POSTGRES_PASSWORD",
            "ENG_APP_DB_PASSWORD",
            "DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "KEYCLOAK_ADMIN_PASSWORD",
        )
        for key in required_keys:
            assert key in kv, f"Missing required variable {key} in backend/.env.prod.example"

        assert kv["APP_ENV"] == "production"
        assert kv["SEED_DEV_DATA"] == "false"
        assert kv["KC_PROXY_HEADERS"] == "xforwarded"
        assert "0123456789abcdef" not in raw
        assert "eng_app_pass_change_in_prod" not in raw
        assert "manta_password" not in raw
        assert kv["KEYCLOAK_ADMIN_PASSWORD"] != "admin"
