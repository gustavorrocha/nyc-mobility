"""Schemas raw/core/analytics/metadata e tabelas de controle em metadata.

Revision ID: 0001
Revises:
Create Date: 2026-11-06
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

PERIOD_CHECK = r"CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$')"

UPGRADE = [
    "CREATE SCHEMA raw",
    "CREATE SCHEMA core",
    "CREATE SCHEMA analytics",
    "CREATE SCHEMA metadata",
    """
    CREATE TABLE metadata.sources (
        source_id   smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source      text NOT NULL,          -- ex.: tlc, openmeteo
        dataset     text NOT NULL,          -- ex.: yellow, taxi_zones, era5
        created_at  timestamptz NOT NULL DEFAULT now(),
        UNIQUE (source, dataset)
    )
    """,
    f"""
    CREATE TABLE metadata.source_files (
        source_file_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source_id           smallint NOT NULL REFERENCES metadata.sources,
        period              text {PERIOD_CHECK},  -- NULL para referências sem competência
        sha256              char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
        landing_path        text NOT NULL,
        size_bytes          bigint NOT NULL CHECK (size_bytes >= 0),
        acquisition_params  jsonb NOT NULL DEFAULT '{{}}',
        acquired_at         timestamptz NOT NULL DEFAULT now(),
        UNIQUE NULLS NOT DISTINCT (source_id, period, sha256)
    )
    """,
    f"""
    CREATE TABLE metadata.ingestion_runs (
        run_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source_id       smallint NOT NULL REFERENCES metadata.sources,
        period          text NOT NULL {PERIOD_CHECK},
        source_file_id  bigint REFERENCES metadata.source_files,
        status          text NOT NULL
                        CHECK (status IN ('running', 'succeeded', 'failed', 'skipped')),
        started_at      timestamptz NOT NULL DEFAULT now(),
        finished_at     timestamptz,
        rows_read       bigint,
        rows_inserted   bigint,
        rows_flagged    bigint,
        error           text,
        details         jsonb NOT NULL DEFAULT '{{}}'
    )
    """,
    "CREATE INDEX ON metadata.ingestion_runs (source_id, period)",
]

DOWNGRADE = [
    "DROP TABLE metadata.ingestion_runs",
    "DROP TABLE metadata.source_files",
    "DROP TABLE metadata.sources",
    # Sem CASCADE: falha se houver outros objetos, evitando perda de dados silenciosa.
    "DROP SCHEMA metadata",
    "DROP SCHEMA analytics",
    "DROP SCHEMA core",
    "DROP SCHEMA raw",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
