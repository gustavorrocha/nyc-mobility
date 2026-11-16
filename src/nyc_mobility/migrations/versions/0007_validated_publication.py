"""Área de consumo estável e identificação da publicação validada.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA serving")
    op.execute("GRANT USAGE ON SCHEMA serving TO dashboard_reader")
    op.execute("""
        CREATE TABLE metadata.analytics_publication (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            run_id bigint NOT NULL REFERENCES metadata.analytics_runs,
            published_at timestamptz NOT NULL
        )
    """)
    op.execute("GRANT SELECT ON metadata.analytics_publication TO dashboard_reader")


def downgrade() -> None:
    for table in (
        "data_coverage",
        "trip_quality",
        "trip_weather_hourly",
        "trip_demand_hourly",
        "service_types",
        "zones",
    ):
        op.execute(f"DROP TABLE IF EXISTS serving.{table}")
    op.execute("DROP SCHEMA serving")
    op.execute("DROP TABLE metadata.analytics_publication")
