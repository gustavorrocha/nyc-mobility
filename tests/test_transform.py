"""dbt build ponta a ponta sobre fixtures: core preserva tudo, analytics usa só o válido."""

import shutil
import subprocess
from pathlib import Path

import sqlalchemy as sa
from test_weather_zones import weather_json, zones_csv

from nyc_mobility.ingest import ingest

DBT_DIR = Path(__file__).parents[1] / "dbt"


def rows(engine, sql):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql)).all()


def test_dbt_build_core_and_analytics(engine, landing, yellow_parquet, tmp_path, monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", engine.url.database)
    ingest(
        "tlc.taxi_zones",
        url=zones_csv(tmp_path / "z.csv", ['1,"EWR","A","EWR"', '2,"Q","B",""']),
        force=True,
    )
    # 2024-02: zonas conhecidas; 2024-03: embarque numa zona fora do cadastro.
    ingest("tlc.yellow", "2024-02", url=yellow_parquet(tmp_path / "a" / "y.parquet", "2024-02"))
    ingest(
        "tlc.yellow",
        "2024-03",
        url=yellow_parquet(tmp_path / "b" / "y.parquet", "2024-03", pu_location=99),
    )
    ingest("openmeteo.era5", "2024-02", url=weather_json(tmp_path / "w.json", "2024-02"))

    subprocess.run(
        [shutil.which("dbt"), "build", "--project-dir", DBT_DIR, "--profiles-dir", DBT_DIR],
        check=True,
    )

    assert rows(
        engine,
        "SELECT period, invalid_reason, count(*) FROM core.trips "
        "WHERE period IN ('2024-02', '2024-03') GROUP BY 1, 2 ORDER BY 1, 2",
    ) == [
        ("2024-02", "dropoff_before_pickup", 1),
        ("2024-02", None, 4),
        ("2024-03", "dropoff_before_pickup", 1),
        ("2024-03", "unknown_zone", 4),
    ]
    # Fixture: corridas válidas às 11h, 12h, 13h e 14h locais de 01/02 (UTC-5), 15 min cada.
    assert rows(
        engine,
        "SELECT sum(trip_count), min(hour_utc AT TIME ZONE 'UTC')::text, avg(avg_duration_min) "
        "FROM analytics.trip_demand_hourly WHERE hour_local >= '2024-02-01' "
        "AND hour_local < '2024-04-01'",
    ) == [(4, "2024-02-01 16:00:00", 15)]
    # Fevereiro/2024 inteiro (696 h), com zero nas horas sem corrida; março não tem clima.
    assert rows(
        engine,
        "SELECT period, count(*), sum(trip_count), count(*) FILTER (WHERE trip_count = 0), "
        "count(*) FILTER (WHERE precipitation_mm IS NULL) FROM analytics.trip_weather_hourly "
        "WHERE period IN ('2024-02', '2024-03') GROUP BY 1",
    ) == [("2024-02", 696, 4, 692, 1)]
