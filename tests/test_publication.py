import pytest
import sqlalchemy as sa

from dashboard import db as reader
from nyc_mobility import build as runner
from nyc_mobility import db
from nyc_mobility.publication import TABLES


@pytest.fixture
def publication_db(engine, monkeypatch):
    name = "nyc_mobility_test_publication"
    admin = sa.create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        conn.execute(sa.text(f"CREATE DATABASE {name}"))
    monkeypatch.setenv("POSTGRES_DB", name)
    monkeypatch.setenv("DASHBOARD_DB_USER", engine.url.username)
    monkeypatch.setenv("DASHBOARD_DB_PASSWORD", engine.url.password)
    db.upgrade()
    target = sa.create_engine(engine.url.set(database=name), poolclass=sa.pool.NullPool)
    with target.begin() as conn:
        for table, schema in TABLES.items():
            extra = ", period text" if schema == "analytics" else ""
            values = ", '2025-01'" if schema == "analytics" else ""
            if table == "data_coverage":
                extra += ", status text"
                values += ", 'available'"
            conn.execute(sa.text(f"CREATE TABLE {schema}.{table} (value int{extra})"))
            conn.execute(sa.text(f"INSERT INTO {schema}.{table} VALUES (1{values})"))
    yield target
    target.dispose()
    with admin.connect() as conn:
        conn.execute(sa.text(f"DROP DATABASE {name} WITH (FORCE)"))
    admin.dispose()


def finish(engine):
    with engine.connect() as conn:
        run_id = conn.scalar(
            sa.text(
                "INSERT INTO metadata.analytics_runs (status,input_versions) "
                "VALUES ('running','{}') RETURNING run_id"
            )
        )
        conn.commit()
        error = runner._finish(conn, run_id, [], None)
    return run_id, error


def change_sources(engine, value):
    with engine.begin() as conn:
        for table, schema in TABLES.items():
            conn.execute(sa.text(f"UPDATE {schema}.{table} SET value = :value"), {"value": value})


def test_readers_keep_one_version_during_publication(publication_db):
    first, error = finish(publication_db)
    assert error is None
    with reader.snapshot():
        assert reader.query("SELECT value FROM serving.zones") == [{"value": 1}]
        change_sources(publication_db, 2)
        second, error = finish(publication_db)
        assert error is None
        assert reader.query("SELECT value FROM serving.trip_demand_hourly") == [{"value": 1}]
        assert reader.query("SELECT run_id FROM metadata.analytics_publication") == [
            {"run_id": first}
        ]
    assert reader.query("SELECT value FROM serving.zones") == [{"value": 2}]
    assert reader.query("SELECT run_id FROM metadata.analytics_publication") == [{"run_id": second}]


def test_failed_copy_preserves_all_tables_and_pointer(publication_db):
    first, _ = finish(publication_db)
    with publication_db.begin() as conn:
        conn.execute(sa.text("ALTER TABLE serving.data_coverage ADD CHECK (value = 1)"))
    change_sources(publication_db, 2)
    failed, error = finish(publication_db)
    assert "Publicação falhou" in error
    for table in TABLES:
        assert reader.query(f"SELECT value FROM serving.{table}") == [{"value": 1}]
    assert reader.query("SELECT run_id FROM metadata.analytics_publication") == [{"run_id": first}]
    assert reader.query(
        "SELECT status FROM metadata.analytics_runs WHERE run_id = %(id)s", {"id": failed}
    ) == [{"status": "failed"}]


def test_unvalidated_source_changes_do_not_change_consumption(publication_db):
    finish(publication_db)
    change_sources(publication_db, 2)
    assert reader.query("SELECT value FROM serving.trip_demand_hourly") == [{"value": 1}]


def test_schema_change_requires_an_explicit_migration(publication_db):
    with publication_db.begin() as conn:
        conn.execute(sa.text("ALTER TABLE core.zones ADD description text"))
    first, _ = finish(publication_db)
    with publication_db.begin() as conn:
        conn.execute(sa.text("ALTER TABLE core.zones DROP COLUMN description"))
    _, error = finish(publication_db)
    assert error is not None
    assert reader.query("SELECT run_id FROM metadata.analytics_publication") == [{"run_id": first}]


def test_precision_change_is_not_silently_rounded(publication_db):
    with publication_db.begin() as conn:
        conn.execute(sa.text("ALTER TABLE core.zones ALTER value TYPE numeric(5,2)"))
    first, _ = finish(publication_db)
    with publication_db.begin() as conn:
        conn.execute(sa.text("ALTER TABLE core.zones ALTER value TYPE numeric(8,3)"))
        conn.execute(sa.text("UPDATE core.zones SET value = 2.345"))
    _, error = finish(publication_db)
    assert error is not None
    assert reader.query("SELECT value FROM serving.zones") == [{"value": 1}]
    assert reader.query("SELECT run_id FROM metadata.analytics_publication") == [{"run_id": first}]
