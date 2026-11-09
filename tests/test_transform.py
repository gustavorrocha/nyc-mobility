"""dbt build ponta a ponta sobre fixtures: core preserva tudo, analytics usa só o válido."""

import json
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


INCREMENTAL = ("trips", "weather_hourly", "trip_demand_hourly", "trip_weather_hourly")


def dbt_build(*args):
    """Roda `dbt build` e devolve as linhas inseridas por modelo incremental."""
    subprocess.run(
        [shutil.which("dbt"), "build", "--project-dir", DBT_DIR, "--profiles-dir", DBT_DIR, *args],
        check=True,
    )
    results = json.loads((DBT_DIR / "target" / "run_results.json").read_text(encoding="utf-8"))
    affected = {
        r["unique_id"].rsplit(".", 1)[-1]: r["adapter_response"].get("rows_affected")
        for r in results["results"]
        if r["unique_id"].startswith("model.")
    }
    return {name: affected[name] for name in INCREMENTAL}


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

    dbt_build("--full-refresh")

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
    # Dashboard: dbt concede leitura só aos modelos liberados (+grants em dbt_project.yml).
    assert rows(
        engine,
        "SELECT t, has_table_privilege('dashboard_reader', t, 'SELECT') FROM unnest(ARRAY["
        "'analytics.trip_demand_hourly', 'analytics.trip_weather_hourly', 'core.zones', "
        "'core.service_types', 'core.trips', 'core.weather_hourly']) AS t ORDER BY t",
    ) == [
        ("analytics.trip_demand_hourly", True),
        ("analytics.trip_weather_hourly", True),
        ("core.service_types", True),
        ("core.trips", False),
        ("core.weather_hourly", False),
        ("core.zones", True),
    ]


def test_dbt_incremental_reprocesses_only_changed_periods(
    engine, landing, yellow_parquet, tmp_path
):
    # Continua o estado do teste anterior (banco de teste da sessão).
    def count(sql):
        return rows(engine, sql)[0][0]

    # Sem mudança nos dados: nada é reprocessado.
    assert dbt_build() == dict.fromkeys(INCREMENTAL, 0)

    # Clima de março/2024 (743 h, horário de verão): reprocessa o clima de março e o
    # viagens x clima de fevereiro (última hora ganha os acumulados) e de março.
    ingest("openmeteo.era5", "2024-03", url=weather_json(tmp_path / "w.json", "2024-03"))
    assert dbt_build() == {
        "trips": 0,
        "weather_hourly": 743,
        "trip_weather_hourly": 696 + 743,
        "trip_demand_hourly": 0,
    }
    assert (
        count(
            "SELECT count(*) FROM analytics.trip_weather_hourly "
            "WHERE period = '2024-02' AND precipitation_mm IS NULL"
        )
        == 0
    )

    # Março republicado (3 corridas, 2 válidas em horas distintas): só março é refeito.
    ingest(
        "tlc.yellow", "2024-03", url=yellow_parquet(tmp_path / "c" / "y.parquet", "2024-03", rows=3)
    )
    assert dbt_build() == {
        "trips": 3,
        "weather_hourly": 0,
        "trip_demand_hourly": 2,
        "trip_weather_hourly": 743,
    }
    assert count("SELECT count(*) FROM core.trips WHERE period = '2024-02'") == 5

    # Nova versão do cadastro de zonas: todas as competências de viagens são reprocessadas.
    ingest(
        "tlc.taxi_zones",
        url=zones_csv(tmp_path / "z2.csv", ['1,"EWR","A","EWR"', '2,"Q","B",""', '3,"M","C",""']),
    )
    built = dbt_build()
    assert built["trips"] == count("SELECT count(*) FROM core.trips")
    assert built["trip_demand_hourly"] == count("SELECT count(*) FROM analytics.trip_demand_hourly")
    assert built["weather_hourly"] == 0
    assert built["trip_weather_hourly"] == count(
        "SELECT count(*) FROM analytics.trip_weather_hourly"
    )


def test_dst_local_times_are_flagged_and_resolved(engine, landing, yellow_parquet, tmp_path):
    # Volta do horário de verão em 02/11/2025: 01:00–01:59 ocorre em EDT (UTC-4) e depois em
    # EST (UTC-5). Início em 09/03/2025: 02:00–02:59 não existe.
    fall = [
        ("2025-11-02T01:40", "2025-11-02T02:05"),  # embarque ambíguo → EST (25 min)
        ("2025-11-02T00:50", "2025-11-02T01:10"),  # desembarque ambíguo → EDT (20 min)
        ("2025-11-02T01:20", "2025-11-02T01:35"),  # ambos ambíguos, empate → 1ª ocorrência
        ("2025-11-02T01:50", "2025-11-02T01:10"),  # "antes" no relógio, mas EDT → EST (20 min)
        ("2025-11-02T10:00", "2025-11-02T10:15"),  # comum
    ]
    spring = [
        ("2025-03-09T02:30", "2025-03-09T02:45"),  # inexistente
        ("2025-03-09T01:50", "2025-03-09T03:10"),  # atravessa o salto (20 min)
    ]
    ingest(
        "tlc.yellow", "2025-11", url=yellow_parquet(tmp_path / "f.parquet", "2025-11", times=fall)
    )
    ingest(
        "tlc.yellow", "2025-03", url=yellow_parquet(tmp_path / "s.parquet", "2025-03", times=spring)
    )
    dbt_build()

    assert rows(
        engine,
        "SELECT source_row_number, local_time_issue, "
        "to_char(pickup_at_utc AT TIME ZONE 'UTC', 'HH24:MI'), duration_seconds / 60, "
        "invalid_reason FROM core.trips WHERE period IN ('2025-03', '2025-11') "
        "ORDER BY period DESC, source_row_number",
    ) == [
        (1, "ambiguous", "06:40", 25, None),
        (2, "ambiguous", "04:50", 20, None),
        (3, "ambiguous", "05:20", 15, None),
        (4, "ambiguous", "05:50", 20, None),
        (5, None, "15:00", 15, None),
        (1, "nonexistent", None, None, "nonexistent_local_time"),
        (2, None, "06:50", 20, None),
    ]
