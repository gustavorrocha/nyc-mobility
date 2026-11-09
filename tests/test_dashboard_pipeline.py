"""Página Pipeline Health: regras de conciliação/resumo e visualizações (consultas em
test_transform)."""

from datetime import UTC, datetime, timedelta

from dashboard import pipeline

T0 = datetime(2026, 11, 9, 15, tzinfo=UTC)


def run(run_id, status, dataset="tlc.yellow", period="2025-01", minutes=0, inserted=None, msg=None):
    started = T0 + timedelta(minutes=minutes)
    return {
        "run_id": run_id,
        "dataset": dataset,
        "period": period,
        "status": status,
        "started_at": started,
        "finished_at": started + timedelta(seconds=30),
        "seconds": 30.0,
        "rows_read": inserted,
        "rows_inserted": inserted,
        "rows_flagged": 0 if inserted else None,
        "message": msg,
    }


RUNS = [
    run(1, "succeeded", inserted=100),
    run(2, "skipped", minutes=5, msg="versão (hash) já publicada"),
    run(3, "succeeded", minutes=10, inserted=105),  # republicação: esta é a publicada
    run(4, "failed", minutes=15, msg="ConcurrentLoadError: carga concorrente"),
    run(5, "succeeded", dataset="openmeteo.era5", minutes=20, inserted=744),
]
QUALITY = [
    {"period": "2025-01", "status": "ok", "reason": None, "trips": 95},
    {"period": "2025-01", "status": "metric_issue", "reason": "implausible_amount", "trips": 7},
    {"period": "2025-01", "status": "invalid", "reason": "unknown_zone", "trips": 3},
]
CONSUMPTION = [
    {
        "period": "2025-01",
        "valid": 102,
        "measured": 95,
        "weather_hours": 744,
        "expected_hours": 744,
        "precipitation_hours": 743,
    }
]


def test_published_is_latest_successful_run_per_dataset_and_period():
    pub = pipeline.published(RUNS)
    assert pub[("tlc.yellow", "2025-01")]["run_id"] == 3
    assert pub[("openmeteo.era5", "2025-01")]["run_id"] == 5


def test_reconciliation_checks_raw_core_and_analytics():
    (row,) = pipeline.reconciliation(RUNS, QUALITY, CONSUMPTION)
    assert (row["raw"], row["core"], row["invalid"], row["valid"]) == (105, 105, 3, 102)
    assert row["ok"]
    lost = [{**CONSUMPTION[0], "valid": 101}]  # uma corrida válida não chegou ao analytics
    assert not pipeline.reconciliation(RUNS, QUALITY, lost)[0]["ok"]
    stale = [r for r in RUNS if r["run_id"] != 3]  # raw (100) ≠ core (105)
    assert not pipeline.reconciliation(stale, QUALITY, CONSUMPTION)[0]["ok"]


def test_summary_and_kpis():
    recon = pipeline.reconciliation(RUNS, QUALITY, CONSUMPTION)
    s = pipeline.summary(RUNS, recon, now=T0 + timedelta(hours=3))
    assert (s["runs"], s["succeeded"], s["skipped"], s["failed"]) == (5, 3, 1, 1)
    assert s["last_success"] == T0 + timedelta(minutes=20, seconds=30)
    assert s["reconciled"] and s["periods"] == ["2025-01"]
    values = [t.children[1].children for t in pipeline.kpi_tiles(s)]
    assert values == ["há 3 h", "5", "20,0%", "✓ OK", "93,1%"]
    assert pipeline.kpi_tiles(s)[0].children[2].children == "09/11/2026 15:20 UTC"
    empty = pipeline.summary([], [], now=T0)
    assert pipeline.kpi_tiles(empty)[3].children[1].children == "✕ Divergente"


def test_figures_and_tables_render():
    timeline = pipeline.timeline_figure(RUNS)
    assert [t.name for t in timeline.data] == ["✓ Sucesso", "↷ Sem mudança", "✕ Falha"]
    quality = pipeline.quality_figure(QUALITY)
    assert [t.name for t in quality.data] == [
        "Inválida (fora de todas as métricas)",
        "Métrica implausível (fora das médias)",
    ]
    assert list(quality.data[1].text) == ["7 (6,67%)"]
    assert pipeline._share(217, 7_052_499) == "<0,01%"
    recon = pipeline.reconciliation_rows(pipeline.reconciliation(RUNS, QUALITY, CONSUMPTION))
    assert recon[0]["weather"] == "744 / 744 (1 sem precipitação)"
    assert (recon[0]["check"], recon[0]["_tone"]) == ("✓ OK", "good")
    runs = pipeline.run_rows(RUNS)
    assert (runs[3]["status"], runs[3]["_tone"]) == ("✕ Falha", "critical")
    assert runs[1]["inserted"] == "—"
    table = pipeline._table(pipeline.RUN_COLUMNS, runs, left=("status",))
    first_row = table.children.children[1].children[0].children
    assert first_row[1].className == "left tone-good"  # status com cor + ícone no texto
    assert pipeline.timeline_figure([]).layout.annotations
    assert pipeline.quality_figure(QUALITY[:1]).layout.annotations
