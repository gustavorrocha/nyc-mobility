"""Histórico de transformações dbt e suas versões de entrada.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        CREATE TABLE metadata.analytics_runs (
            run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            period text CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
            status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            finished_at timestamptz,
            input_versions jsonb NOT NULL,
            validated_periods text[] NOT NULL DEFAULT '{}',
            node_results jsonb NOT NULL DEFAULT '[]',
            error text
        )
    """)
    op.execute("GRANT SELECT ON metadata.analytics_runs TO dashboard_reader")


def downgrade() -> None:
    op.execute("DROP TABLE metadata.analytics_runs")
