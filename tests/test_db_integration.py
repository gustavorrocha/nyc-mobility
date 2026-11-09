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


def test_migrations_are_reversible(engine, monkeypatch):
    """upgrade → downgrade base → upgrade num banco vazio próprio (o da sessão tem dados)."""
    name = "nyc_mobility_test_migrations"
    admin = sa.create_engine(
        engine.url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=sa.pool.NullPool,
    )
    with admin.connect() as conn:
        conn.execute(sa.text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        conn.execute(sa.text(f"CREATE DATABASE {name}"))
    monkeypatch.setenv("POSTGRES_DB", name)
    try:
        db.upgrade()
        db.downgrade("base")
        assert sorted(db.missing_schemas()) == sorted(db.SCHEMAS)
        db.upgrade()
        assert db.missing_schemas() == []
    finally:
        with admin.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
