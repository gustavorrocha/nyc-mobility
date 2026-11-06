from datetime import datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import sqlalchemy as sa

from nyc_mobility import db
from nyc_mobility.config import database_url

TEST_DB = "nyc_mobility_test"


@pytest.fixture(scope="session")
def engine():
    """Banco descartável `nyc_mobility_test`, migrado; o banco de desenvolvimento não é tocado.

    Os testes de integração são pulados se o PostgreSQL (variáveis POSTGRES_*) não responder.
    """
    try:
        admin = sa.create_engine(
            database_url().set(database="postgres"),
            isolation_level="AUTOCOMMIT",
            poolclass=sa.pool.NullPool,
        )
        with admin.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
            conn.execute(sa.text(f"CREATE DATABASE {TEST_DB}"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL indisponível: {exc}")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("POSTGRES_DB", TEST_DB)
        db.upgrade()
        yield sa.create_engine(database_url(), poolclass=sa.pool.NullPool)


def _yellow_parquet(path, period, rows=5, pu_location=1, drop_columns=()):
    """Parquet mínimo no formato Yellow 2025; a 1ª corrida termina antes de começar."""
    start = datetime.fromisoformat(f"{period}-01T10:00:00")
    pickups = [start + timedelta(hours=i) for i in range(rows)]
    dropoffs = [p + timedelta(minutes=-5 if i == 0 else 15) for i, p in enumerate(pickups)]
    columns = {
        "VendorID": pa.array([1] * rows, pa.int32()),
        "tpep_pickup_datetime": pa.array(pickups, pa.timestamp("us")),
        "tpep_dropoff_datetime": pa.array(dropoffs, pa.timestamp("us")),
        "passenger_count": pa.array([1] * (rows - 1) + [None], pa.int64()),
        "trip_distance": pa.array([1.5] * rows, pa.float64()),
        "store_and_fwd_flag": pa.array(["N"] * rows, pa.large_string()),
        "PULocationID": pa.array([pu_location] * rows, pa.int64()),
        "DOLocationID": pa.array([2] * rows, pa.int32()),
        "total_amount": pa.array([10.0] * rows, pa.float64()),
        "Airport_fee": pa.array([0.0] * rows, pa.float64()),
    }
    for name in drop_columns:
        del columns[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(columns), path)
    return path.as_uri()


@pytest.fixture
def landing(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_MOBILITY_LANDING_DIR", str(tmp_path / "landing"))
    return tmp_path / "landing"


@pytest.fixture
def yellow_parquet():
    return _yellow_parquet
