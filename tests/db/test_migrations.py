"""Alembic migration tests."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def fresh_migration_db():
    """Create an isolated schema and yield its connection URL."""
    raw_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://rai_user:rai_password@localhost:5434/rai_gateway",
    )
    base_url = raw_url.split("?")[0]
    schema = f"migrate_test_{uuid.uuid4().hex}"

    base_engine = create_engine(base_url)
    with base_engine.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        conn.commit()

    schema_url = f"{base_url}?options=-csearch_path%3D{schema}"
    yield schema_url

    with base_engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()
    base_engine.dispose()


def _configure_alembic(cfg: Config, database_url: str) -> None:
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    os.environ["TEST_DATABASE_URL"] = database_url


def test_empty_db_upgrade_head(fresh_migration_db):
    """Fresh empty database can migrate to head via Alembic only."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    _configure_alembic(cfg, fresh_migration_db)

    command.upgrade(cfg, "head")

    engine = create_engine(fresh_migration_db)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    engine.dispose()

    expected = {"prompts", "prompt_versions", "api_keys", "executions_audit"}
    assert expected.issubset(tables)


def test_migration_downgrade_base(fresh_migration_db):
    """The single-organization migration cannot be reversed."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    _configure_alembic(cfg, fresh_migration_db)

    command.upgrade(cfg, "head")
    with pytest.raises(RuntimeError, match="cannot be reversed"):
        command.downgrade(cfg, "base")


def test_onboarding_intake_migration_upgrade_preserves_data_and_downgrades(
    fresh_migration_db,
):
    """f2a3b4c5d6e7 against its immediate predecessor, in an isolated schema."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    _configure_alembic(cfg, fresh_migration_db)
    command.upgrade(cfg, "e1f2a3b4c5d6")

    engine = create_engine(fresh_migration_db)
    job_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO background_jobs (id, job_type, status, attempt_count, max_attempts) "
                "VALUES (:id, 'PORTFOLIO_REPORT', 'PENDING', 0, 5)"
            ),
            {"id": job_id},
        )

    command.upgrade(cfg, "f2a3b4c5d6e7")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "onboarding_sessions",
        "onboarding_sources",
        "onboarding_source_revisions",
        "onboarding_runs",
        "onboarding_mutation_receipts",
    } <= tables
    columns = {c["name"] for c in inspector.get_columns("background_jobs")}
    assert {"worker_id", "lease_token", "lease_expires_at", "heartbeat_at"} <= columns
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT job_type, lease_token FROM background_jobs WHERE id = :id"),
            {"id": job_id},
        ).one()
    assert row.job_type == "PORTFOLIO_REPORT" and row.lease_token is None

    # Only one active run per session is enforced by a partial unique index.
    index_names = {i["name"] for i in inspect(engine).get_indexes("onboarding_runs")}
    assert "uq_onb_one_active_run" in index_names

    command.downgrade(cfg, "e1f2a3b4c5d6")
    inspector = inspect(engine)
    assert "onboarding_sessions" not in set(inspector.get_table_names())
    assert "lease_token" not in {
        c["name"] for c in inspector.get_columns("background_jobs")
    }
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM background_jobs")).scalar() == 1

    command.upgrade(cfg, "head")
    assert "onboarding_runs" in set(inspect(engine).get_table_names())
    engine.dispose()


def test_onboarding_extraction_migration_against_its_predecessor(fresh_migration_db):
    """a3b4c5d6e7f8 on top of f2a3b4c5d6e7: preserves Step 02 rows, then reverses."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    _configure_alembic(cfg, fresh_migration_db)
    command.upgrade(cfg, "f2a3b4c5d6e7")

    engine = create_engine(fresh_migration_db)
    session_id, source_id, job_id, run_id = (uuid.uuid4() for _ in range(4))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO onboarding_sessions (id, title, lifecycle, row_version, input_revision,"
                " created_by, updated_by) VALUES (:id, 't', 'OPEN', 1, 0, 'u', 'u')"
            ),
            {"id": session_id},
        )
        conn.execute(
            text(
                "INSERT INTO onboarding_sources (id, session_id, kind, role, included, created_by)"
                " VALUES (:id, :s, 'TEXT', 'USER_PROMPT', true, 'u')"
            ),
            {"id": source_id, "s": session_id},
        )
        conn.execute(
            text(
                "INSERT INTO background_jobs (id, job_type, status, attempt_count, max_attempts)"
                " VALUES (:id, 'ONBOARDING_PREPARE_SOURCES', 'SUCCEEDED', 1, 5)"
            ),
            {"id": job_id},
        )
        conn.execute(
            text(
                "INSERT INTO onboarding_runs (id, session_id, run_number, stage, input_revision,"
                " manifest, manifest_digest, job_id, created_by, ended_at) VALUES (:id, :s, 1,"
                " 'PREPARE_SOURCES', 0, CAST(:m AS json), 'abc', :j, 'u', now())"
            ),
            {
                "id": run_id,
                "s": session_id,
                "j": job_id,
                "m": '{"version": 1, "entries": []}',
            },
        )

    command.upgrade(cfg, "a3b4c5d6e7f8")
    inspector = inspect(engine)
    assert {
        "onboarding_source_extractions",
        "onboarding_extracted_blocks",
        "onboarding_run_sources",
        "onboarding_ai_configuration",
        "onboarding_provider_probes",
    } <= set(inspector.get_table_names())
    run_cols = {c["name"] for c in inspector.get_columns("onboarding_runs")}
    assert {"preparation_run_id", "plan", "plan_digest"} <= run_cols
    with engine.connect() as conn:
        legacy = conn.execute(
            text("SELECT manifest, plan_digest FROM onboarding_runs WHERE id = :id"),
            {"id": run_id},
        ).one()
        options = conn.execute(
            text(
                "SELECT parsing_options_version FROM onboarding_sources WHERE id = :id"
            ),
            {"id": source_id},
        ).scalar()
    assert (
        legacy.plan_digest is None and options == 1
    )  # historical v1 manifest untouched
    assert (
        '"version": 1' in str(legacy.manifest).replace("'", '"')
        or legacy.manifest["version"] == 1
    )
    probe_indexes = {
        i["name"] for i in inspector.get_indexes("onboarding_provider_probes")
    }
    assert "uq_onb_one_active_probe" in probe_indexes

    command.downgrade(cfg, "f2a3b4c5d6e7")
    inspector = inspect(engine)
    assert "onboarding_source_extractions" not in set(inspector.get_table_names())
    assert "plan_digest" not in {
        c["name"] for c in inspector.get_columns("onboarding_runs")
    }
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM onboarding_runs")).scalar() == 1
    command.upgrade(cfg, "head")
    engine.dispose()
