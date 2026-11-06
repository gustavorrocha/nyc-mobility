"""Integração com PostgreSQL: pulado se o banco não estiver acessível via POSTGRES_*."""

import pytest
import sqlalchemy as sa

from nyc_mobility import db
from nyc_mobility.config import database_url


@pytest.fixture(scope="module")
def engine():
    try:
        engine = sa.create_engine(database_url(), poolclass=sa.pool.NullPool)
        engine.connect().close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL indisponível: {exc}")
    return engine


def test_upgrade_is_idempotent_and_creates_schemas(engine):
    db.upgrade()
    db.upgrade()
    assert db.missing_schemas() == []
    with engine.connect() as conn:
        query = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'metadata'"
        tables = set(conn.scalars(sa.text(query)))
    assert tables == {"sources", "source_files", "ingestion_runs"}
