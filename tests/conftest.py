import os
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
        if os.environ.get("CI"):  # no CI o banco é obrigatório: falhar em vez de pular
            raise
        pytest.skip(f"PostgreSQL indisponível: {exc}")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("POSTGRES_DB", TEST_DB)
        db.upgrade()
        yield sa.create_engine(database_url(), poolclass=sa.pool.NullPool)


def _yellow_parquet(
    path, period, rows=5, pu_location=1, drop_columns=(), times=None, distances=None, amounts=None
):
    """Parquet mínimo no formato Yellow 2025; a 1ª corrida termina antes de começar.

    `times`: lista de (embarque, desembarque) em ISO local, substituindo as corridas padrão.
    `distances` / `amounts`: valores por corrida (padrão: 1,5 mi e US$ 10).
    """
    if times:
        rows = len(times)
        pickups = [datetime.fromisoformat(p) for p, _ in times]
        dropoffs = [datetime.fromisoformat(d) for _, d in times]
    else:
        start = datetime.fromisoformat(f"{period}-01T10:00:00")
        pickups = [start + timedelta(hours=i) for i in range(rows)]
        dropoffs = [p + timedelta(minutes=-5 if i == 0 else 15) for i, p in enumerate(pickups)]
    columns = {
        "VendorID": pa.array([1] * rows, pa.int32()),
        "tpep_pickup_datetime": pa.array(pickups, pa.timestamp("us")),
        "tpep_dropoff_datetime": pa.array(dropoffs, pa.timestamp("us")),
        "passenger_count": pa.array([1] * (rows - 1) + [None], pa.int64()),
        "trip_distance": pa.array(distances or [1.5] * rows, pa.float64()),
        "store_and_fwd_flag": pa.array(["N"] * rows, pa.large_string()),
        "PULocationID": pa.array([pu_location] * rows, pa.int64()),
        "DOLocationID": pa.array([2] * rows, pa.int32()),
        "total_amount": pa.array(amounts or [10.0] * rows, pa.float64()),
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


def _tlc_parquet(path, dataset, period, rows=3, pu_location=1, do_location=2):
    """Parquet mínimo de green, fhv ou fhvhv (formato 2025): corridas de 15 min a partir das 10h
    locais do dia 1º. `pu_location`: valor único ou lista por corrida (None = zona ausente)."""
    start = datetime.fromisoformat(f"{period}-01T10:00:00")
    pickups = [start + timedelta(hours=i) for i in range(rows)]
    times = {
        "pickup": pa.array(pickups, pa.timestamp("us")),
        "dropoff": pa.array([p + timedelta(minutes=15) for p in pickups], pa.timestamp("us")),
    }
    pu = pu_location if isinstance(pu_location, list) else [pu_location] * rows
    do = [do_location] * rows

    def const(value, kind=None):
        return pa.array([value] * rows, kind or pa.float64())

    if dataset == "green":
        columns = {
            "lpep_pickup_datetime": times["pickup"],
            "lpep_dropoff_datetime": times["dropoff"],
            "PULocationID": pa.array(pu, pa.int32()),
            "DOLocationID": pa.array(do, pa.int32()),
            "trip_distance": const(2.0),
            "total_amount": const(12.0),
            "trip_type": const(1, pa.int64()),
        }
    elif dataset == "fhv":
        columns = {
            "dispatching_base_num": const("B00001", pa.large_string()),
            "pickup_datetime": times["pickup"],
            "dropOff_datetime": times["dropoff"],
            "PUlocationID": pa.array(pu, pa.int64()),
            "DOlocationID": pa.array(do, pa.int64()),
        }
    else:  # fhvhv: 3 mi em 15 min; valor pago = 20 + 0,5 + 1,5 + 2,75 + 3 = 27,75
        columns = {
            "hvfhs_license_num": const("HV0003", pa.large_string()),
            "pickup_datetime": times["pickup"],
            "dropoff_datetime": times["dropoff"],
            "PULocationID": pa.array(pu, pa.int32()),
            "DOLocationID": pa.array(do, pa.int32()),
            "trip_miles": const(3.0),
            "trip_time": const(900, pa.int64()),
            "base_passenger_fare": const(20.0),
            "bcf": const(0.5),
            "sales_tax": const(1.5),
            "congestion_surcharge": const(2.75),
            "tips": const(3.0),
            "driver_pay": const(15.0),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(columns), path)
    return path.as_uri()


@pytest.fixture
def tlc_parquet():
    return _tlc_parquet
