"""Página Mobilidade e clima: regras de comparação e visualizações (consulta em test_transform)."""

from datetime import datetime, timedelta

import pytest

from dashboard import climate

MONDAY_8H = datetime(2025, 1, 6, 8)
WEEK = timedelta(days=7)


def hour(at, trips, minutes=10.0, mph=12.0, price=20.0, precipitation=0.0, snowfall=0.0, temp=0.0):
    """Hora com `trips` corridas medidas de `minutes` min, a `mph`, custando `price`."""
    duration = trips * minutes
    return {
        "hour_local": at,
        "trips": trips,
        "measured": trips,
        "duration_min_sum": duration,
        "distance_miles_sum": duration / 60 * mph,
        "amount_sum": trips * price,
        "precipitation_mm": precipitation,
        "snowfall_cm": snowfall,
        "temperature_c": temp,
    }


@pytest.mark.parametrize(
    ("precipitation", "snowfall", "expected"),
    [(0.0, 0.0, "Seco"), (0.1, 0.0, "Chuva"), (0.4, 0.3, "Neve"), (None, None, None)],
)
def test_condition(precipitation, snowfall, expected):
    assert (
        climate.condition(hour(MONDAY_8H, 1, precipitation=precipitation, snowfall=snowfall))
        == expected
    )


def test_effects_compare_each_metric_with_dry_hours_of_same_weekday_and_hour():
    hours = climate.classify(
        [
            hour(MONDAY_8H, 100),
            hour(MONDAY_8H + WEEK, 300),  # seco: 200 corridas/h, 10 min, 12 mph, US$ 20
            hour(MONDAY_8H + 2 * WEEK, 240, minutes=12, mph=10, price=22, precipitation=1.0),
            hour(MONDAY_8H + timedelta(hours=1), 50, precipitation=1.0),  # 9h: sem seca igual
        ]
    )
    base = climate.baselines(hours)
    rain = climate.by_condition(hours, base)["Chuva"]
    assert rain["hours"] == 2
    assert rain["demand"] == pytest.approx(0.2)  # 240 / 200
    assert rain["duration"] == pytest.approx(0.2)  # 12 / 10 min
    assert rain["speed"] == pytest.approx(10 / 12 - 1)
    assert rain["amount"] == pytest.approx(0.1)  # 22 / 20
    assert climate.by_condition(hours, base)["Neve"]["demand"] is None
    assert rain["distance"] == pytest.approx(0.0)  # 12 min a 10 mph = 2 mi, como no seco
    speed = next(m for m in climate.METRICS if m.key == "speed")
    assert climate.dry_value(hours, speed) == pytest.approx(12)  # mph


def test_weighting_uses_measured_trips_not_hours():
    """Duração esperada pondera pelas corridas medidas de cada hora (razão de somas)."""
    hours = climate.classify(
        [
            hour(MONDAY_8H, 100, minutes=10),
            hour(MONDAY_8H + WEEK, 300, minutes=20),  # seco: 7.000 min / 400 = 17,5 min
            hour(MONDAY_8H + 2 * WEEK, 10, minutes=21, precipitation=0.5),
        ]
    )
    rain = climate.by_condition(hours, climate.baselines(hours))["Chuva"]
    assert rain["duration"] == pytest.approx(21 / 17.5 - 1)


def test_intensity_day_parts_and_temperature_groups():
    dry = [hour(MONDAY_8H + i * WEEK, 100, temp=1.5) for i in range(12)]
    few = [hour(MONDAY_8H + i * WEEK + timedelta(hours=1), 90, temp=-7) for i in range(3)]
    wet = [
        hour(MONDAY_8H + 20 * WEEK, 110, precipitation=0.4),
        hour(MONDAY_8H + 21 * WEEK, 130, precipitation=2.5),
    ]
    hours = climate.classify(dry + few + wet)
    base = climate.baselines(hours)
    weak, strong = climate.by_intensity(hours, base)["Chuva"]
    assert weak["demand"] == pytest.approx(0.1) and strong["demand"] == pytest.approx(0.3)
    morning = climate.by_day_part(hours, base)["Chuva"][1]
    assert morning["hours"] == 2
    labels, rows = climate.by_temperature(hours, base)
    assert labels == ["0°"] and rows[0]["hours"] == 12  # faixa de -9 °C: só 3 horas


def test_cards_and_figures_render():
    hours = climate.classify(
        [hour(MONDAY_8H, 100), hour(MONDAY_8H + WEEK, 120, minutes=11, precipitation=1.0)]
    )
    base = climate.baselines(hours)
    cards = climate.metric_cards(hours, climate.by_condition(hours, base))
    duration = cards[1].children
    assert duration[0].children == "Duração da corrida"
    assert duration[1].children[1].children == "+10,0%"  # chuva
    assert duration[2].children[1].children == "—"  # sem horas de neve
    assert duration[3].children == "seco: 10,0 min"
    fig = climate.effects_figure(
        ["Fraca", "Forte"], [("Chuva", climate.by_intensity(hours, base)["Chuva"])]
    )
    assert len(fig.data) == len(climate.METRICS)  # um painel por métrica
    assert [a.text for a in fig.layout.annotations] == [m.label for m in climate.METRICS]
    empty = climate.effects_figure(["Fraca"], [("Neve", [{"hours": 0}])])
    assert empty.layout.annotations[0].text.startswith("Sem horas")


def test_update_without_period_returns_empty_page():
    cards, *figures = climate.update(None, None, None, None, ["yellow"])
    assert cards == [] and len(figures) == 3
