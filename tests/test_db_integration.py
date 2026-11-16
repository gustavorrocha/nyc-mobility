"""Integração com PostgreSQL: pulado se o banco não estiver acessível via POSTGRES_*."""

import sqlalchemy as sa
from alembic import command

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
        "metadata.analytics_runs",
        "raw.tlc_yellow_trips",
        "raw.tlc_taxi_zones",
        "raw.openmeteo_weather_hourly",
        "raw.tlc_green_trips",
        "raw.tlc_fhv_trips",
        "raw.tlc_fhvhv_trips",
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
        # A revisão 0005 já existe em bancos que receberam a ingestão de outras modalidades.
        # O rastreamento deve ser aplicado depois dela, sem reinterpretar a mesma revisão.
        command.upgrade(db._config(), "0005")
        legacy = sa.create_engine(engine.url.set(database=name), poolclass=sa.pool.NullPool)
        with legacy.connect() as conn:
            assert conn.scalar(sa.text("SELECT to_regclass('metadata.analytics_runs')")) is None
        db.upgrade()
        assert db.missing_schemas() == []
        with legacy.connect() as conn:
            assert conn.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0006"
            assert conn.scalar(sa.text("SELECT to_regclass('metadata.analytics_runs')")) is not None
            assert conn.scalar(sa.text("SELECT to_regclass('raw.tlc_green_trips')")) is not None
        legacy.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
