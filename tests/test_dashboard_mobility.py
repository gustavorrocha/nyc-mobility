"""Página Mobilidade urbana: filtros e visualizações (as consultas são testadas em
test_transform.py, sobre o analytics produzido pelo dbt)."""

import json
from datetime import date, datetime
from pathlib import Path

from dashboard import mobility


def test_params_make_end_exclusive_and_empty_zones_mean_all():
    p = mobility.params("2025-01-01", "2025-01-31T00:00:00", None, [], ["yellow"])
    assert p == {
        "start": date(2025, 1, 1),
        "end": date(2025, 2, 1),
        "borough": None,
        "zones": None,
        "services": ["yellow"],
    }
    assert len(mobility.days_in(p)) == 31


def test_kpi_tiles_format_pt_br_and_handle_no_data():
    k = {
        "trips": 7052499,
        "measured": 6724130,
        "duration_min": 14.62,
        "distance_mi": 3.0712,
        "speed_mph": 12.61,
        "amount_per_trip": 26.0512,
    }
    tiles = mobility.kpi_tiles(k, 59)
    assert [t.children[1].children for t in tiles] == [
        "7.052.499",
        "14,6 min",
        "3,07 mi",
        "12,6 mph",
        "US$ 26,05",
    ]
    assert tiles[0].children[2].children == "119.534 por dia"
    assert tiles[1].children[2].children == "base: 95,3% das corridas"
    empty = mobility.kpi_tiles(dict.fromkeys(k), 59)
    assert [t.children[1].children for t in empty] == ["—", "— min", "— mi", "— mph", "—"]


def test_heatmap_averages_by_weekday_occurrences():
    # 01/01/2025 (quarta) a 14/01/2025: duas quartas, nenhuma ausente.
    days = [date(2025, 1, d) for d in range(1, 15)]
    fig = mobility.heatmap_figure([{"dow": 3, "hour": 8, "trips": 100}], days)
    z = fig.data[0].z
    assert z[2][8] == 50  # 100 corridas / 2 quartas
    assert z[2][9] == 0  # hora sem corridas
    # Um único dia: os demais dias da semana ficam vazios, não zero.
    z = mobility.heatmap_figure([{"dow": 3, "hour": 8, "trips": 100}], days[:1]).data[0].z
    assert z[2][8] == 100 and z[0][8] is None


def test_ranking_keeps_homonymous_zones_apart():
    rows = [
        {"zone_id": 56, "zone": "Corona", "borough": "Queens", "trips": 20, "share": 0.5},
        {"zone_id": 57, "zone": "Corona", "borough": "Queens", "trips": 10, "share": 0.25},
    ]
    bar = mobility.ranking_figure(rows).data[0]
    assert list(bar.y) == [0, 1] and list(bar.x) == [20, 10]
    assert list(bar.text) == ["20 · 50,0%", "10 · 25,0%"]


def test_empty_results_render_message():
    for fig in (
        mobility.trend_figure([{"t": datetime(2025, 1, 1), "trips": 0}], "day"),
        mobility.heatmap_figure([], [date(2025, 1, 1)]),
        mobility.ranking_figure([]),
    ):
        assert fig.layout.annotations[0].text.startswith("Sem dados")


def test_update_without_period_returns_empty_page():
    kpis, *figures = mobility.update(None, None, None, None, ["yellow"])
    assert len(figures) == 3  # um valor por Output (o Dash não aceita listas aninhadas)
    assert kpis == [] and all(f.layout.annotations for f in figures)


def test_map_uses_log_scale_and_highlights_selected_zones():
    rows = [
        {"zone_id": 132, "zone": "JFK Airport", "borough": "Queens", "trips": 250000},
        {"zone_id": 7, "zone": "Astoria", "borough": "Queens", "trips": 30},
    ]
    trace = mobility.map_figure(rows, [7]).data[0]
    assert trace.geojson == mobility.GEOJSON_URL  # URL: o navegador baixa a geometria uma vez
    assert list(trace.locations) == [132, 7]
    assert list(trace.colorbar.ticktext) == ["10", "100", "1.000", "10.000", "100.000"]
    assert list(trace.selectedpoints) == [1]
    assert mobility.map_figure(rows, None).data[0].selectedpoints is None


def test_click_on_map_toggles_zone_in_filter():
    click = {"points": [{"location": 7}]}
    assert mobility.toggle_zone(click, None) == ([7], None)
    assert mobility.toggle_zone(click, [4, 7]) == ([4], None)
    assert mobility.toggle_zone(None, [4]) == (mobility.no_update, mobility.no_update)


def test_geojson_asset_covers_all_taxi_zones():
    path = Path(mobility.__file__).parent / "assets" / "taxi_zones.geojson"
    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    assert sorted(f["properties"]["zone_id"] for f in features) == list(range(1, 264))
    lon, lat = features[0]["geometry"]["coordinates"][0][0]  # zona 1 (EWR), em WGS84
    assert -74.3 < lon < -74.1 and 40.6 < lat < 40.8
