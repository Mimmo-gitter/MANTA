import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy.schema import ForeignKeyConstraint, UniqueConstraint
import yaml

from app.db.models import Base
from scripts import verify_migrations

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
VERSIONS_DIR = BACKEND_DIR / "migrations" / "versions"
INITIAL_MIGRATION_PATH = VERSIONS_DIR / "02ec69277327_initial_core_schema.py"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


class TestMigrationsCICheck(unittest.TestCase):
    def test_initial_migration_matches_models_and_is_reversible(self) -> None:
        """Verify 02ec69277327 matches Base.metadata and downgrade() is a valid exact inverse."""
        tree = ast.parse(INITIAL_MIGRATION_PATH.read_text(encoding="utf-8"))

        upgrade_tables: list[str] = []
        downgrade_tables: list[str] = []
        upgrade_indexes: list[tuple[str, str]] = []
        downgrade_indexes: list[tuple[str, str]] = []
        dropped_tables_so_far: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
                for stmt in node.body:
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                        attr = getattr(stmt.value.func, "attr", None)
                        if attr == "create_table":
                            tname = stmt.value.args[0].value
                            upgrade_tables.append(tname)
                            table = Base.metadata.tables[tname]

                            mig_pks: list[str] = []
                            mig_uqs: list[tuple[tuple[str, ...], str | None]] = []
                            mig_fks: list[tuple[tuple[str, ...], tuple[str, ...], str | None]] = []
                            mig_cols: dict[str, bool | None] = {}

                            for arg in stmt.value.args[1:]:
                                if not isinstance(arg, ast.Call):
                                    continue
                                item_attr = getattr(arg.func, "attr", None)
                                if item_attr == "Column":
                                    cname = arg.args[0].value
                                    kw = {k.arg: ast.literal_eval(k.value) for k in arg.keywords}
                                    mig_cols[cname] = kw.get("nullable")
                                elif item_attr == "PrimaryKeyConstraint":
                                    mig_pks = [a.value for a in arg.args]
                                elif item_attr == "UniqueConstraint":
                                    kw = {k.arg: k.value.value for k in arg.keywords}
                                    uq_cols = tuple(a.value for a in arg.args)
                                    mig_uqs.append((uq_cols, kw.get("name")))
                                elif item_attr == "ForeignKeyConstraint":
                                    kw = {k.arg: k.value.value for k in arg.keywords}
                                    cols = tuple(e.value for e in arg.args[0].elts)
                                    refs = tuple(e.value for e in arg.args[1].elts)
                                    mig_fks.append((cols, refs, kw.get("ondelete")))

                            model_cols = {c.name: c.nullable for c in table.columns}
                            self.assertEqual(mig_cols, model_cols, f"Column mismatch in {tname}")

                            model_pks = [c.name for c in table.primary_key.columns]
                            self.assertEqual(mig_pks, model_pks, f"PK mismatch in {tname}")

                            model_uqs = [
                                (tuple(c.name for c in cstr.columns), cstr.name)
                                for cstr in table.constraints
                                if isinstance(cstr, UniqueConstraint)
                            ]
                            self.assertEqual(set(mig_uqs), set(model_uqs), f"UQ diff in {tname}")

                            model_fks = [
                                (
                                    tuple(fk.parent.name for fk in cstr.elements),
                                    tuple(fk.target_fullname for fk in cstr.elements),
                                    cstr.ondelete,
                                )
                                for cstr in table.constraints
                                if isinstance(cstr, ForeignKeyConstraint)
                            ]
                            self.assertEqual(set(mig_fks), set(model_fks), f"FK diff in {tname}")

                        elif attr == "create_index":
                            idx_pair = (stmt.value.args[0].value, stmt.value.args[1].value)
                            upgrade_indexes.append(idx_pair)

            elif isinstance(node, ast.FunctionDef) and node.name == "downgrade":
                for stmt in node.body:
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                        attr = getattr(stmt.value.func, "attr", None)
                        if attr == "drop_table":
                            tname = stmt.value.args[0].value
                            downgrade_tables.append(tname)
                            dropped_tables_so_far.add(tname)
                        elif attr == "drop_index":
                            idx_name = stmt.value.args[0].value
                            kw = {k.arg: k.value.value for k in stmt.value.keywords}
                            tname = kw.get("table_name", "")
                            self.assertNotIn(
                                tname,
                                dropped_tables_so_far,
                                f"Index {idx_name} dropped after table {tname} was already dropped",
                            )
                            downgrade_indexes.append((idx_name, tname))

        meta_tables = set(Base.metadata.tables.keys())
        self.assertEqual(set(upgrade_tables), meta_tables)
        self.assertEqual(downgrade_tables, list(reversed(upgrade_tables)))

        meta_indexes = {
            (idx.name, tname)
            for tname, table in Base.metadata.tables.items()
            for idx in table.indexes
        }
        self.assertEqual(set(upgrade_indexes), meta_indexes)
        self.assertEqual(set(downgrade_indexes), meta_indexes)

        # Verify FK creation order in upgrade()
        created: set[str] = set()
        for tname in upgrade_tables:
            for fk in Base.metadata.tables[tname].foreign_keys:
                target_table = fk.column.table.name
                if target_table != tname:
                    self.assertIn(
                        target_table,
                        created,
                        f"Table {tname} references {target_table} before it is created",
                    )
            created.add(tname)

        # Verify FK drop order in downgrade()
        remaining = set(upgrade_tables)
        for tname in downgrade_tables:
            remaining.remove(tname)
            for other in remaining:
                for fk in Base.metadata.tables[other].foreign_keys:
                    self.assertNotEqual(
                        fk.column.table.name,
                        tname,
                        f"Table {tname} dropped while {other} still references it",
                    )

    def test_alembic_offline_upgrade_and_downgrade_sql(self) -> None:
        """Verify Alembic executes upgrade head and downgrade head:base cleanly from repo root."""
        up_out = verify_migrations.run_alembic_command(
            "upgrade head (offline)",
            ["upgrade", "head", "--sql"],
            cwd=REPO_ROOT,
        )
        self.assertIn("Running upgrade  -> 02ec69277327", up_out)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector;", up_out)
        self.assertIn("CREATE TABLE tenants", up_out)

        down_out = verify_migrations.run_alembic_command(
            "downgrade base (offline)",
            ["downgrade", "head:base", "--sql"],
            cwd=REPO_ROOT,
        )
        self.assertIn("Running downgrade 02ec69277327 ->", down_out)
        self.assertIn("DROP TABLE tenants;", down_out)

    def test_deliberately_broken_downgrade_fails_and_prints_revision(self) -> None:
        """AC 2: A broken downgrade() must fail the job and print the failing revision."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            versions_dir = tmp_path / "versions"
            versions_dir.mkdir(parents=True, exist_ok=True)

            original_source = INITIAL_MIGRATION_PATH.read_text(encoding="utf-8")
            broken_target = 'def downgrade() -> None:\n    """Downgrade schema."""'
            broken_replacement = (
                'def downgrade() -> None:\n    """Downgrade schema."""\n'
                '    raise RuntimeError("Deliberately broken downgrade() for CI verification")'
            )
            broken_source = original_source.replace(broken_target, broken_replacement)
            self.assertNotEqual(original_source, broken_source)
            (versions_dir / INITIAL_MIGRATION_PATH.name).write_text(broken_source, encoding="utf-8")

            base_ini = (BACKEND_DIR / "alembic.ini").read_text(encoding="utf-8")
            script_loc = (BACKEND_DIR / "migrations").as_posix()
            ver_loc = versions_dir.as_posix()
            custom_ini = base_ini.replace(
                "script_location = %(here)s/migrations",
                f"script_location = {script_loc}\nversion_locations = {ver_loc}",
            ).replace(
                "prepend_sys_path = %(here)s",
                f"prepend_sys_path = {BACKEND_DIR.as_posix()}",
            )
            tmp_ini_path = tmp_path / "alembic_broken_down.ini"
            tmp_ini_path.write_text(custom_ini, encoding="utf-8")

            with self.assertRaises(verify_migrations.MigrationCheckError) as ctx:
                verify_migrations.run_alembic_command(
                    "downgrade base",
                    ["downgrade", "head:base", "--sql"],
                    ini_path=tmp_ini_path,
                    cwd=BACKEND_DIR,
                )

            err = ctx.exception
            self.assertNotEqual(err.returncode, 0)
            self.assertEqual(err.failing_revision, "02ec69277327")
            self.assertIn("02ec69277327", err.message)
            self.assertIn(
                "ERROR [alembic.env] Migration failed during downgrade at revision: 02ec69277327",
                err.output,
            )

    def test_deliberately_broken_upgrade_fails_and_prints_revision(self) -> None:
        """A broken upgrade() must also fail the job and print the failing revision."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            versions_dir = tmp_path / "versions"
            versions_dir.mkdir(parents=True, exist_ok=True)

            original_source = INITIAL_MIGRATION_PATH.read_text(encoding="utf-8")
            broken_target = 'def upgrade() -> None:\n    """Upgrade schema."""'
            broken_replacement = (
                'def upgrade() -> None:\n    """Upgrade schema."""\n'
                '    raise RuntimeError("Deliberately broken upgrade() for CI verification")'
            )
            broken_source = original_source.replace(broken_target, broken_replacement)
            (versions_dir / INITIAL_MIGRATION_PATH.name).write_text(broken_source, encoding="utf-8")

            base_ini = (BACKEND_DIR / "alembic.ini").read_text(encoding="utf-8")
            script_loc = (BACKEND_DIR / "migrations").as_posix()
            ver_loc = versions_dir.as_posix()
            custom_ini = base_ini.replace(
                "script_location = %(here)s/migrations",
                f"script_location = {script_loc}\nversion_locations = {ver_loc}",
            ).replace(
                "prepend_sys_path = %(here)s",
                f"prepend_sys_path = {BACKEND_DIR.as_posix()}",
            )
            tmp_ini_path = tmp_path / "alembic_broken_up.ini"
            tmp_ini_path.write_text(custom_ini, encoding="utf-8")

            with self.assertRaises(verify_migrations.MigrationCheckError) as ctx:
                verify_migrations.run_alembic_command(
                    "upgrade head (initial)",
                    ["upgrade", "head", "--sql"],
                    ini_path=tmp_ini_path,
                    cwd=BACKEND_DIR,
                )

            err = ctx.exception
            self.assertNotEqual(err.returncode, 0)
            self.assertEqual(err.failing_revision, "02ec69277327")
            self.assertIn(
                "ERROR [alembic.env] Migration failed during upgrade at revision: 02ec69277327",
                err.output,
            )

    def test_schema_drift_check_fails_when_autogenerate_detects_diffs(self) -> None:
        """Scope 3: Verify `--step check` fails when schema drift is detected."""
        drift_out = (
            "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n"
            "FAILED: New upgrade operations detected: [('add_column', None, 'users', "
            "Column('unmigrated_col', String()))]\n"
        )
        subprocess_result = type(
            "CompletedProcess",
            (),
            {"returncode": 1, "stdout": drift_out},
        )()
        with patch("scripts.verify_migrations.subprocess.run", return_value=subprocess_result):
            exit_code = verify_migrations.main(["--step", "check"])
            self.assertEqual(exit_code, 1)

    def test_ci_workflow_migration_job_invariants(self) -> None:
        """Verify .github/workflows/ci.yml defines backend-migrations and runs on every PR."""
        workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
        # PyYAML parses bare `on:` as boolean True in YAML 1.1
        triggers = workflow.get("on") or workflow.get(True)
        self.assertIsNotNone(triggers)
        self.assertIn("pull_request", triggers)
        pr_trigger = triggers["pull_request"] or {}
        self.assertNotIn(
            "branches",
            pr_trigger,
            "pull_request trigger should not restrict target branches so it runs on every PR",
        )

        jobs = workflow.get("jobs", {})
        self.assertIn("backend-migrations", jobs)
        mig_job = jobs["backend-migrations"]

        # Verify PostgreSQL + pgvector service
        postgres_svc = mig_job.get("services", {}).get("postgres", {})
        self.assertEqual(postgres_svc.get("image"), "pgvector/pgvector:pg16")
        self.assertIn("pg_isready", postgres_svc.get("options", ""))

        # Verify asyncpg DATABASE_URL
        db_url = mig_job.get("env", {}).get("DATABASE_URL", "")
        self.assertTrue(db_url.startswith("postgresql+asyncpg://"))

        # Verify sequence: upgrade -> downgrade -> reupgrade -> check
        step_commands = [s.get("run", "") for s in mig_job.get("steps", []) if "run" in s]
        joined_commands = "\n".join(step_commands)
        idx_up1 = joined_commands.find("--step upgrade")
        idx_down = joined_commands.find("--step downgrade")
        idx_up2 = joined_commands.find("--step reupgrade")
        idx_check = joined_commands.find("--step check")

        self.assertGreaterEqual(idx_up1, 0, "Missing `--step upgrade` in backend-migrations")
        self.assertGreater(idx_down, idx_up1, "`--step downgrade` must run after `--step upgrade`")
        self.assertGreater(
            idx_up2, idx_down, "`--step reupgrade` must run after `--step downgrade`"
        )
        self.assertGreater(idx_check, idx_up2, "`--step check` must run after `--step reupgrade`")

        # Verify ci-gate requires backend-migrations
        ci_gate = jobs.get("ci-gate", {})
        self.assertIn("backend-migrations", ci_gate.get("needs", []))
        gate_script = "\n".join(s.get("run", "") for s in ci_gate.get("steps", []) if "run" in s)
        self.assertIn("needs.backend-migrations.result", gate_script)


if __name__ == "__main__":
    unittest.main()
