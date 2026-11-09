"""dbt build ponta a ponta sobre fixtures: core preserva tudo, analytics usa só o válido."""

import json
import shutil
import subprocess
from pathlib import Path

import sqlalchemy as sa
from test_weather_zones import weather_json, zones_csv

from dashboard import climate, mobility, pipeline
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
        "SELECT sum(trip_count), min(hour_utc AT TIME ZONE 'UTC')::text, "
        "sum(duration_min_sum) / sum(measured_trip_count) "
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
    # Médias por hora: 1,5 mi em 15 min (6 mph) e US$ 10 por corrida.
    assert set(
        rows(
            engine,
            "SELECT trip_count, avg_duration_min, avg_distance_miles, avg_speed_mph, avg_amount "
            "FROM analytics.trip_weather_hourly WHERE period = '2024-02' AND trip_count > 0",
        )
    ) == {(1, 15.0, 1.5, 6.0, 10.0)}
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


def test_dashboard_mobility_queries(engine, monkeypatch):
    """Consultas da página Mobilidade sobre o analytics do teste anterior (fevereiro/2024)."""
    monkeypatch.setenv("POSTGRES_DB", engine.url.database)
    monkeypatch.setenv("DASHBOARD_DB_USER", engine.url.username)
    monkeypatch.setenv("DASHBOARD_DB_PASSWORD", engine.url.password)
    # 01/02/2024 (quinta): corridas válidas às 11h, 12h, 13h e 14h na zona 1 ("A", EWR).
    p = mobility.params("2024-02-01", "2024-02-01", None, None, ["yellow"])
    assert mobility.load_kpis(p) == {
        "trips": 4,
        "measured": 4,
        "duration_min": 15.0,
        "distance_mi": 1.5,
        "speed_mph": 6.0,
        "amount_per_trip": 10.0,
    }
    trend = mobility.load_trend(p, "hour")
    assert len(trend) == 24
    assert [(r["t"].hour, r["trips"]) for r in trend if r["trips"]] == [
        (11, 1),
        (12, 1),
        (13, 1),
        (14, 1),
    ]
    heatmap = sorted((r["dow"], r["hour"], r["trips"]) for r in mobility.load_heatmap(p))
    assert heatmap == [(4, 11, 1), (4, 12, 1), (4, 13, 1), (4, 14, 1)]
    assert [(r["zone"], r["trips"], r["share"]) for r in mobility.load_ranking(p)] == [
        ("A", 4, 1.0)
    ]
    for other in ({"borough": "Q"}, {"zones": [2]}, {"services": ["green"]}):
        assert mobility.load_kpis({**p, **other})["trips"] is None
    # Mapa: ignora o filtro de zonas (todas continuam clicáveis), mas respeita os demais.
    expected = [{"zone_id": 1, "zone": "A", "borough": "EWR", "trips": 4}]
    assert mobility.load_zone_trips({**p, "zones": [2]}) == expected
    assert mobility.load_zone_trips({**p, "borough": "Q"}) == []
    # Clima: todas as horas do mês com clima; corridas filtradas, zero nas demais.
    month = mobility.params("2024-02-01", "2024-02-29", None, None, ["yellow"])
    hours = climate.load_hours(month)
    assert len(hours) == 696 and sum(h["trips"] for h in hours) == 4
    assert sum(h["trips"] for h in climate.load_hours({**month, "borough": "Q"})) == 0


def test_dashboard_pipeline_queries(engine, monkeypatch):
    """Pipeline Health sobre o banco do teste: metadata das ingestões + analytics do dbt."""
    monkeypatch.setenv("POSTGRES_DB", engine.url.database)
    monkeypatch.setenv("DASHBOARD_DB_USER", engine.url.username)
    monkeypatch.setenv("DASHBOARD_DB_PASSWORD", engine.url.password)
    runs = pipeline.load_runs()
    assert {"tlc.yellow", "tlc.taxi_zones", "openmeteo.era5"} <= {r["dataset"] for r in runs}
    recon = {
        r["period"]: r
        for r in pipeline.reconciliation(runs, pipeline.load_quality(), pipeline.load_consumption())
    }
    # 2024-02: 5 linhas, 1 inválida; 2024-03: todas inválidas (nada chega ao analytics).
    feb, mar = recon["2024-02"], recon["2024-03"]
    assert (feb["raw"], feb["core"], feb["invalid"], feb["valid"], feb["ok"]) == (5, 5, 1, 4, True)
    assert (feb["weather_hours"], feb["expected_hours"]) == (696, 696)
    assert (mar["raw"], mar["core"], mar["invalid"], mar["valid"], mar["ok"]) == (
        5,
        5,
        5,
        None,
        True,
    )


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


def test_implausible_metrics_count_as_demand_but_not_in_averages(
    engine, landing, yellow_parquet, tmp_path
):
    """2024-06: uma corrida normal e uma por regra de metric_issue (limites do dbt_project.yml)."""
    times = [
        ("2024-06-03T08:00:00", "2024-06-03T08:20:00"),  # normal: 2 mi, 20 min, US$ 20
        ("2024-06-03T08:05:00", "2024-06-03T08:05:30"),  # 30 s
        ("2024-06-03T08:10:00", "2024-06-03T08:30:00"),  # distância zero
        ("2024-06-03T08:15:00", "2024-06-03T08:25:00"),  # 90 mi em 10 min (540 mph)
        ("2024-06-03T08:20:00", "2024-06-03T08:40:00"),  # estorno (valor negativo)
    ]
    url = yellow_parquet(
        tmp_path / "m" / "y.parquet",
        "2024-06",
        times=times,
        distances=[2.0, 1.0, 0.0, 90.0, 2.0],
        amounts=[20.0, 8.0, 5.0, 300.0, -20.0],
    )
    ingest("tlc.yellow", "2024-06", url=url)
    dbt_build()

    assert rows(
        engine,
        "SELECT metric_issue FROM core.trips WHERE period = '2024-06' ORDER BY source_row_number",
    ) == [
        (None,),
        ("implausible_duration",),
        ("implausible_distance",),
        ("implausible_speed",),
        ("implausible_amount",),
    ]
    assert rows(
        engine,
        "SELECT trip_count, measured_trip_count, duration_min_sum, distance_miles_sum, amount_sum "
        "FROM analytics.trip_demand_hourly WHERE period = '2024-06'",
    ) == [(5, 1, 20.0, 2.0, 20.0)]
