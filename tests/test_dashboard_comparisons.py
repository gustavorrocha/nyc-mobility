from datetime import date

import pytest

from dashboard import comparisons, mobility


def test_previous_period_has_same_length_across_leap_day_and_year_boundary():
    p = mobility.params("2024-03-01", "2024-03-02", "Queens", [7], ["yellow"])
    previous = comparisons.previous_period(p)
    assert previous == {**p, "start": date(2024, 2, 28), "end": date(2024, 3, 1)}
    january = mobility.params("2025-01-01", "2025-01-31", None, None, ["yellow"])
    assert comparisons.previous_period(january)["start"] == date(2024, 12, 1)


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [(120, 100, 0.2), (0, 100, -1), (0, 0, None), (100, 0, None), (None, 100, None)],
)
def test_relative_change_does_not_invent_a_zero_baseline(current, previous, expected):
    actual = comparisons.relative_change(current, previous)
    assert actual is None if expected is None else actual == pytest.approx(expected)


def test_service_trends_keep_missing_data_as_gaps_and_distinguish_modalities():
    rows = [
        {"t": date(2025, 1, 1), "service_type": "yellow", "trips": 20},
        {"t": date(2025, 2, 1), "service_type": "yellow", "trips": None},
        {"t": date(2025, 1, 1), "service_type": "green", "trips": 0},
        {"t": date(2025, 2, 1), "service_type": "green", "trips": None},
    ]
    figure = comparisons.service_trend_figure(rows, "day")
    assert [trace.name for trace in figure.data] == ["Yellow Taxi", "Green Taxi"]
    assert list(figure.data[0].y) == [20, None]
    assert list(figure.data[1].y) == [0, None]
    assert all(trace.connectgaps is False for trace in figure.data)


def test_market_share_uses_the_selected_services_and_preserves_unavailable_metrics():
    rows = [
        {
            "service_type": "yellow",
            "trips": 30,
            "measured": 30,
            "duration_min": 15,
            "distance_mi": 2,
            "speed_mph": 8,
            "amount_per_trip": 12,
        },
        {
            "service_type": "fhv",
            "trips": 10,
            "measured": 0,
            "duration_min": None,
            "distance_mi": None,
            "speed_mph": None,
            "amount_per_trip": None,
        },
    ]
    bar = comparisons.share_figure(rows).data[0]
    assert list(bar.x) == [75, 25]
    table = comparisons.service_rows(rows)
    assert table[1]["amount_per_trip"] == "—"
    assert table[1]["coverage"] == "0,0%"


def test_incomplete_period_hides_deltas_even_if_partial_totals_exist():
    current = {
        "trips": 120,
        "duration_min": 15,
        "distance_mi": 2,
        "speed_mph": 8,
        "amount_per_trip": 12,
    }
    previous = {**current, "trips": 100}
    rows = comparisons.period_rows(current, previous, complete=False)
    assert all(r["change"] == "—" for r in rows)
    assert comparisons.period_rows(current, previous, complete=True)[0]["change"] == "+20,0%"


def test_period_callback_hides_partial_comparison_and_explains_dates(monkeypatch):
    monkeypatch.setattr(
        comparisons.coverage,
        "load",
        lambda p: (
            [
                {"period": "2025-01", "service_type": "yellow", "status": "available"},
            ]
            if p["start"].year == 2025
            else []
        ),
    )
    monkeypatch.setattr(
        comparisons.mobility, "load_kpis", lambda p: {key: 100 for key in comparisons.METRICS}
    )
    note, result = comparisons.update_periods("2025-01-01", "2025-01-31", None, [], ["yellow"])
    assert "01/12/2024 a 31/12/2024 (0/31 dias cobertos)" in note
    assert "Cobertura incompleta" in note
    rows = result.children.children[1].children
    assert all(row.children[-1].children == "—" for row in rows)


def test_callbacks_handle_empty_selection_without_queries(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Empty filters must not query the database")

    monkeypatch.setattr(comparisons, "query", unexpected)
    assert comparisons.update_services("2025-01-01", "2025-01-02", None, [], [])[2] == []
    assert comparisons.update_periods(None, None, None, [], [])[1] == []


def test_share_chart_distinguishes_missing_coverage_from_zero_demand():
    missing = comparisons.share_figure([{"service_type": "yellow", "trips": None}])
    zero = comparisons.share_figure([{"service_type": "yellow", "trips": 0}])
    assert "Sem cobertura" in missing.layout.annotations[0].text
    assert "Sem corridas" in zero.layout.annotations[0].text
