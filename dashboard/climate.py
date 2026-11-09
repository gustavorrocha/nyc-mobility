"""Página 2 — Mobilidade e clima: como chuva, neve e temperatura mudam demanda, duração,
distância, velocidade e valor das corridas.

Uma consulta traz, por hora, as somas filtradas de analytics.trip_demand_hourly (aceita os
filtros de zona e modalidade) e o clima da cidade (analytics.trip_weather_hourly, ponto único).
O restante é agregação em Python sobre no máximo alguns milhares de horas.

Comparação justa: chove mais de dia, quando há mais corridas e mais trânsito. Cada métrica é
comparada ao "esperado" pelas horas secas com o mesmo dia da semana e hora local, no período
filtrado. Para uma métrica numerador/denominador (ex.: minutos / corridas medidas):

    razão esperada do horário = Σ numerador / Σ denominador nas horas secas daquele horário
    efeito de um grupo de horas = Σ numerador / Σ (denominador × razão esperada) − 1

Demanda é o caso de denominador 1 por hora (corridas por hora).
"""

import math
from collections import defaultdict
from typing import NamedTuple

from dash import Input, Output, callback, html
from plotly.subplots import make_subplots

from dashboard import theme
from dashboard.db import query
from dashboard.mobility import SOURCE, WHERE, params

DRY, RAIN, SNOW = "Seco", "Chuva", "Neve"
COLORS = {RAIN: "#2a78d6", SNOW: "#eb6834", DRY: "#2a78d6"}  # paleta de referência, slots 1-2
DAY_PARTS = [("Madrugada", 0, 6), ("Manhã", 6, 12), ("Tarde", 12, 18), ("Noite", 18, 24)]
INTENSE_MM = 1.0  # mm/h: a partir daqui, precipitação "forte"
TEMP_BIN_C = 3
MIN_HOURS = 10  # faixas de temperatura com menos horas ficam de fora (média instável)


class Metric(NamedTuple):
    key: str
    label: str
    numerator: str
    denominator: str | None  # None: por hora
    scale: float  # razão seca -> unidade exibida
    unit: str
    decimals: int


METRICS = [
    Metric("demand", "Demanda", "trips", None, 1, "corridas/h", 0),
    Metric("duration", "Duração da corrida", "duration_min_sum", "measured", 1, "min", 1),
    Metric("distance", "Distância da corrida", "distance_miles_sum", "measured", 1, "mi", 2),
    Metric("speed", "Velocidade média", "distance_miles_sum", "duration_min_sum", 60, "mph", 1),
    Metric("amount", "Valor da corrida", "amount_sum", "measured", 1, "US$", 2),
]


def load_hours(p: dict) -> list[dict]:
    """Uma linha por hora do período com clima; horas sem corridas filtradas valem zero."""
    return query(
        f"""WITH demand AS (
                SELECT d.hour_utc, sum(d.trip_count)::bigint AS trips,
                       sum(d.measured_trip_count)::bigint AS measured,
                       sum(d.duration_min_sum) AS duration_min_sum,
                       sum(d.distance_miles_sum) AS distance_miles_sum,
                       sum(d.amount_sum) AS amount_sum
                FROM {SOURCE} WHERE {WHERE} GROUP BY 1
            )
            SELECT w.hour_local, w.temperature_c, w.precipitation_mm, w.snowfall_cm,
                   coalesce(demand.trips, 0) AS trips,
                   coalesce(demand.measured, 0) AS measured,
                   coalesce(demand.duration_min_sum, 0) AS duration_min_sum,
                   coalesce(demand.distance_miles_sum, 0) AS distance_miles_sum,
                   coalesce(demand.amount_sum, 0) AS amount_sum
            FROM analytics.trip_weather_hourly AS w
            LEFT JOIN demand USING (hour_utc)
            WHERE w.hour_local >= %(start)s AND w.hour_local < %(end)s
            ORDER BY w.hour_local""",
        p,
    )


# --- Regras ------------------------------------------------------------------------------


def condition(h: dict) -> str | None:
    """Neve se nevou na hora; chuva se houve precipitação; None sem dado de precipitação.

    ERA5 registra precipitação em passos de 0,1 mm, então "> 0" já é precipitação mensurável.
    """
    if h["precipitation_mm"] is None:
        return None
    if h["snowfall_cm"]:
        return SNOW
    return RAIN if h["precipitation_mm"] > 0 else DRY


def _slot(h: dict) -> tuple[int, int]:
    return h["hour_local"].isoweekday(), h["hour_local"].hour


def _den(h: dict, m: Metric) -> float:
    return 1 if m.denominator is None else h[m.denominator]


def classify(hours: list[dict]) -> list[dict]:
    return [{**h, "condition": condition(h)} for h in hours]


def baselines(hours: list[dict]) -> dict[str, dict[tuple[int, int], float]]:
    """Razão esperada por métrica e horário (dia da semana, hora), a partir das horas secas."""
    result = {}
    for m in METRICS:
        num, den = defaultdict(float), defaultdict(float)
        for h in hours:
            if h["condition"] == DRY:
                num[_slot(h)] += h[m.numerator]
                den[_slot(h)] += _den(h, m)
        result[m.key] = {slot: num[slot] / den[slot] for slot in num if den[slot]}
    return result


def effect(group: list[dict], m: Metric, base: dict) -> float | None:
    """Σ numerador / Σ (denominador × razão esperada) − 1, só onde há esperado."""
    observed = expected = 0.0
    for h in group:
        ratio = base[m.key].get(_slot(h))
        if ratio is not None and _den(h, m):
            observed += h[m.numerator]
            expected += _den(h, m) * ratio
    return observed / expected - 1 if expected else None


def effects(group: list[dict], base: dict) -> dict:
    return {"hours": len(group), **{m.key: effect(group, m, base) for m in METRICS}}


def dry_value(hours: list[dict], m: Metric) -> float | None:
    dry = [h for h in hours if h["condition"] == DRY]
    den = sum(_den(h, m) for h in dry)
    return sum(h[m.numerator] for h in dry) / den * m.scale if den else None


def by_condition(hours: list[dict], base: dict) -> dict[str, dict]:
    return {c: effects([h for h in hours if h["condition"] == c], base) for c in (RAIN, SNOW)}


def by_intensity(hours: list[dict], base: dict) -> dict[str, list[dict]]:
    """Chuva e neve fracas (< INTENSE_MM mm/h) e fortes."""
    return {
        c: [
            effects(
                [h for h in hours if h["condition"] == c and h["precipitation_mm"] < INTENSE_MM],
                base,
            ),
            effects(
                [h for h in hours if h["condition"] == c and h["precipitation_mm"] >= INTENSE_MM],
                base,
            ),
        ]
        for c in (RAIN, SNOW)
    }


def by_day_part(hours: list[dict], base: dict) -> dict[str, list[dict]]:
    return {
        c: [
            effects(
                [h for h in hours if h["condition"] == c and lo <= h["hour_local"].hour < hi], base
            )
            for _, lo, hi in DAY_PARTS
        ]
        for c in (RAIN, SNOW)
    }


def by_temperature(hours: list[dict], base: dict) -> tuple[list[str], list[dict]]:
    """Horas secas por faixa de temperatura (o esperado já controla dia da semana e hora)."""
    bins = defaultdict(list)
    for h in hours:
        if h["condition"] == DRY and h["temperature_c"] is not None:
            bins[math.floor(h["temperature_c"] / TEMP_BIN_C) * TEMP_BIN_C].append(h)
    kept = [(low, group) for low, group in sorted(bins.items()) if len(group) >= MIN_HOURS]
    return (
        [f"{low}°" for low, _ in kept],  # rótulo curto: início da faixa
        [effects(group, base) for _, group in kept],
    )


# --- Visualizações -----------------------------------------------------------------------


def _dry_text(m: Metric, value) -> str:
    if value is None:
        return "—"
    if m.unit == "US$":
        return f"US$ {theme.number(value, m.decimals)}"
    return f"{theme.number(value, m.decimals)} {m.unit}"


def metric_cards(hours: list[dict], effects_by_condition: dict[str, dict]) -> list:
    cards = []
    for m in METRICS:
        rows = [
            html.Div(
                [
                    html.Span(c, className="effect-label"),
                    html.Span(
                        theme.percent(effects_by_condition[c][m.key]), className="effect-value"
                    ),
                ],
                className="effect-row",
            )
            for c in (RAIN, SNOW)
        ]
        cards.append(
            html.Div(
                [
                    html.Div(m.label, className="kpi-label"),
                    *rows,
                    html.Div(f"seco: {_dry_text(m, dry_value(hours, m))}", className="kpi-note"),
                ],
                className="kpi card",
            )
        )
    return cards


def effects_figure(categories: list[str], series: list[tuple[str, list[dict]]]):
    """Um painel por métrica, mesma escala em %, para comparar efeitos entre métricas."""
    if not any(r["hours"] for _, rows in series for r in rows):
        return theme.empty("Sem horas com essa condição no período filtrado.")
    fig = make_subplots(
        rows=1,
        cols=len(METRICS),
        shared_yaxes=True,
        horizontal_spacing=0.03,
        subplot_titles=[m.label for m in METRICS],
    )
    theme.style(
        fig,
        barmode="group",
        bargap=0.25,
        bargroupgap=0.08,
        showlegend=len(series) > 1,
        legend={"orientation": "h", "y": -0.18, "x": 0},
        margin={"l": 8, "r": 8, "t": 28, "b": 8},
    )
    for col, m in enumerate(METRICS, start=1):
        for name, rows in series:
            fig.add_bar(
                x=categories,
                y=[100 * r[m.key] if r[m.key] is not None else None for r in rows],
                name=name,
                legendgroup=name,
                showlegend=col == 1,
                marker={"color": COLORS[name], "cornerradius": 3},
                customdata=[r["hours"] for r in rows],
                hovertemplate=(
                    f"{name} · %{{x}}<br>{m.label}: %{{y:+,.1f}}% vs. esperado"
                    "<br>%{customdata} horas<extra></extra>"
                ),
                row=1,
                col=col,
            )
    fig.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=theme.MUTED, zerolinewidth=1)
    fig.update_annotations(font={"size": 12, "color": theme.INK})
    return fig


# --- Página ------------------------------------------------------------------------------


def layout() -> list:
    return [
        html.Div(id="c-cards", className="kpis"),
        html.P(
            "Efeito = variação vs. o esperado pelas horas secas com o mesmo dia da semana e a "
            "mesma hora, no período filtrado. Clima de um único ponto (Central Park).",
            className="chart-note",
        ),
        html.Div(
            className="charts",
            children=[
                theme.chart_card(
                    "Intensidade da precipitação",
                    "c-intensity",
                    f"Fraca: até {theme.number(INTENSE_MM, 1)} mm/h; forte: acima disso.",
                    300,
                    wide=True,
                ),
                theme.chart_card(
                    "Chuva e neve por faixa do dia",
                    "c-dayparts",
                    "Madrugada 0–6h · Manhã 6–12h · Tarde 12–18h · Noite 18–24h.",
                    300,
                    wide=True,
                ),
                theme.chart_card(
                    "Temperatura",
                    "c-temperature",
                    f"Horas secas em faixas de {TEMP_BIN_C} °C, rotuladas pelo início "
                    f"(0° = 0 a {TEMP_BIN_C} °C); só faixas com {MIN_HOURS}+ horas.",
                    300,
                    wide=True,
                ),
            ],
        ),
    ]


@callback(
    Output("c-cards", "children"),
    Output("c-intensity", "figure"),
    Output("c-dayparts", "figure"),
    Output("c-temperature", "figure"),
    Input("f-period", "start_date"),
    Input("f-period", "end_date"),
    Input("f-borough", "value"),
    Input("f-zones", "value"),
    Input("f-service", "value"),
)
def update(start, end, borough, zones, services):
    if not start or not end:
        return [], theme.empty(), theme.empty(), theme.empty()
    hours = classify(load_hours(params(start, end, borough, zones, services)))
    base = baselines(hours)
    intensity = by_intensity(hours, base)
    day_parts = by_day_part(hours, base)
    temp_labels, temp_rows = by_temperature(hours, base)
    return (
        metric_cards(hours, by_condition(hours, base)),
        effects_figure(["Fraca", "Forte"], [(c, intensity[c]) for c in (RAIN, SNOW)]),
        effects_figure([p for p, _, _ in DAY_PARTS], [(c, day_parts[c]) for c in (RAIN, SNOW)]),
        effects_figure(temp_labels, [(DRY, temp_rows)]),
    )
