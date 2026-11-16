from datetime import date

from dashboard import coverage, mobility


def test_only_months_available_for_every_selected_service_count():
    rows = [
        {"period": "2025-01", "service_type": "yellow", "status": "available"},
        {"period": "2025-01", "service_type": "green", "status": "missing"},
        {"period": "2025-02", "service_type": "yellow", "status": "available"},
        {"period": "2025-02", "service_type": "green", "status": "available"},
    ]
    assert coverage.available_periods(rows) == {"2025-02"}
    assert coverage.available_periods([]) == set()
    p = mobility.params("2025-01-30", "2025-02-02", None, None, ["yellow", "green"])
    assert coverage.covered_days(p, rows) == [date(2025, 2, 1), date(2025, 2, 2)]


def test_coverage_notice_names_missing_and_pending_months():
    rows = [
        {"period": "2025-01", "service_type": "yellow", "status": "available"},
        {"period": "2025-02", "service_type": "yellow", "status": "pending"},
        {"period": "2025-03", "service_type": "yellow", "status": "missing"},
    ]
    panel = coverage.notice(rows)
    assert panel.role == "status"
    assert "1 de 3" in panel.children[0].children
    assert [item.children for item in panel.children[1].children] == [
        "2025-01 · yellow: Disponível",
        "2025-02 · yellow: Aguardando atualização analítica",
        "2025-03 · yellow: Sem carga",
    ]


def test_zero_demand_is_a_real_series_and_missing_data_leaves_a_gap():
    fig = mobility.trend_figure(
        [{"t": date(2025, 1, 1), "trips": 0}, {"t": date(2025, 2, 1), "trips": None}],
        "day",
    )
    assert list(fig.data[0].y) == [0, None]
    assert fig.data[0].connectgaps is False
