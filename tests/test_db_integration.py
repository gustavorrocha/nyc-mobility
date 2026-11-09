"""Integração com PostgreSQL: pulado se o banco não estiver acessível via POSTGRES_*."""

import sqlalchemy as sa

from nyc_mobility import db


def test_upgrade_is_idempotent_and_creates_schemas(engine):
    db.upgrade()  # 2ª execução (a 1ª ocorre na fixture)
    assert db.missing_schemas() == []
    with engine.connect() as conn:
        query = (
            "SELECT table_schema || '.' || table_name FROM information_schema.tables "
            "WHERE table_schema IN ('metadata', 'raw')"
        )
        tables = set(conn.scalars(sa.text(query)))
    assert tables == {
        "metadata.sources",
        "metadata.source_files",
        "metadata.ingestion_runs",
        "raw.tlc_yellow_trips",
        "raw.tlc_taxi_zones",
        "raw.openmeteo_weather_hourly",
    }
