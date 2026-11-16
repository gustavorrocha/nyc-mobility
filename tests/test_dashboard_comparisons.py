from datetime import date

from dashboard import comparisons


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


def test_callbacks_handle_empty_selection_without_queries(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Empty filters must not query the database")

    monkeypatch.setattr(comparisons, "query", unexpected)
    assert comparisons.update_services("2025-01-01", "2025-01-02", None, [], [])[2] == []


def test_share_chart_distinguishes_missing_coverage_from_zero_demand():
    missing = comparisons.share_figure([{"service_type": "yellow", "trips": None}])
    zero = comparisons.share_figure([{"service_type": "yellow", "trips": 0}])
    assert "Sem cobertura" in missing.layout.annotations[0].text
    assert "Sem corridas" in zero.layout.annotations[0].text
