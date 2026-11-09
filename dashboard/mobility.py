"""Página 1 — Mobilidade urbana: KPIs, demanda no tempo, heatmap hora × dia e ranking de zonas.

Fonte: analytics.trip_demand_hourly (corridas válidas por hora local × modalidade × zona de
embarque). As médias de distância e duração são ponderadas pelo número de corridas.
"""

from datetime import date, timedelta

from dash import Input, Output, callback, dcc, html

from dashboard import theme
from dashboard.db import query

WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
HOURLY_MAX_DAYS = 7  # até 7 dias, demanda por hora; acima, por dia
TOP_ZONES = 15

SOURCE = "analytics.trip_demand_hourly AS d JOIN core.zones AS z ON z.zone_id = d.pickup_zone_id"
WHERE = """
    d.hour_local >= %(start)s AND d.hour_local < %(end)s
    AND d.service_type = ANY(%(services)s)
    AND (%(borough)s::text IS NULL OR z.borough = %(borough)s)
    AND (%(zones)s::int[] IS NULL OR d.pickup_zone_id = ANY(%(zones)s))
"""


def params(start: str, end: str, borough, zones, services) -> dict:
    """Valores dos filtros globais para as consultas; `end` vira limite exclusivo (dia seguinte)."""
    return {
        "start": date.fromisoformat(start[:10]),
        "end": date.fromisoformat(end[:10]) + timedelta(days=1),
        "borough": borough,
        "zones": zones or None,
        "services": services or [],
    }


def days_in(p: dict) -> list[date]:
    return [p["start"] + timedelta(days=i) for i in range((p["end"] - p["start"]).days)]


# --- Consultas ---------------------------------------------------------------------------


def load_kpis(p: dict) -> dict:
    return query(
        f"""SELECT sum(d.trip_count)::bigint AS trips,
                   sum(d.avg_distance_miles * d.trip_count) / sum(d.trip_count) AS distance_mi,
                   (sum(d.avg_duration_min * d.trip_count) / sum(d.trip_count))::float8
                       AS duration_min,
                   sum(d.total_amount) / sum(d.trip_count) AS amount_per_trip,
                   sum(d.total_amount) AS amount
            FROM {SOURCE} WHERE {WHERE}""",
        p,
    )[0]


def load_trend(p: dict, grain: str) -> list[dict]:
    """Corridas por hora ou dia, com zero nos intervalos sem corridas."""
    return query(
        f"""WITH demand AS (
                SELECT date_trunc(%(grain)s, d.hour_local) AS t, sum(d.trip_count)::bigint AS trips
                FROM {SOURCE} WHERE {WHERE} GROUP BY 1
            )
            SELECT s.t, coalesce(demand.trips, 0) AS trips
            FROM generate_series(
                %(start)s::timestamp, %(end)s::timestamp - %(step)s::interval, %(step)s::interval
            ) AS s(t)
            LEFT JOIN demand USING (t)
            ORDER BY 1""",
        {**p, "grain": grain, "step": f"1 {grain}"},
    )


def load_heatmap(p: dict) -> list[dict]:
    return query(
        f"""SELECT extract(isodow FROM d.hour_local)::int AS dow,
                   extract(hour FROM d.hour_local)::int AS hour,
                   sum(d.trip_count)::bigint AS trips
            FROM {SOURCE} WHERE {WHERE} GROUP BY 1, 2""",
        p,
    )


def load_ranking(p: dict) -> list[dict]:
    return query(
        f"""SELECT z.zone_id, z.zone, z.borough, sum(d.trip_count)::bigint AS trips,
                   sum(d.trip_count)::float8 / sum(sum(d.trip_count)) OVER () AS share
            FROM {SOURCE} WHERE {WHERE}
            GROUP BY 1, 2, 3 ORDER BY trips DESC, z.zone LIMIT %(top)s""",
        {**p, "top": TOP_ZONES},
    )


# --- Visualizações -----------------------------------------------------------------------


def _money(value, decimals: int = 0) -> str:
    return "—" if value is None else f"US$ {theme.number(value, decimals)}"


def kpi_tiles(k: dict, n_days: int) -> list:
    per_day = k["trips"] / n_days if k["trips"] and n_days else None
    tiles = [
        ("Corridas", theme.number(k["trips"]), f"{theme.number(per_day)} por dia"),
        ("Distância média", f"{theme.number(k['distance_mi'], 2)} mi", "por corrida"),
        ("Duração média", f"{theme.number(k['duration_min'], 1)} min", "por corrida"),
        ("Valor médio", _money(k["amount_per_trip"], 2), "total pago por corrida"),
        ("Faturamento", _money(k["amount"]), "total_amount no período"),
    ]
    return [
        html.Div(
            [
                html.Div(label, className="kpi-label"),
                html.Div(value, className="kpi-value"),
                html.Div(note, className="kpi-note"),
            ],
            className="kpi card",
        )
        for label, value, note in tiles
    ]


def trend_figure(rows: list[dict], grain: str):
    if not any(r["trips"] for r in rows):
        return theme.empty()
    fmt = "%d/%m/%Y %Hh" if grain == "hour" else "%d/%m/%Y"
    fig = theme.figure(hovermode="x unified")
    fig.add_scatter(
        x=[r["t"] for r in rows],
        y=[r["trips"] for r in rows],
        mode="lines",
        line={"color": theme.BLUE, "width": 2},
        fill="tozeroy",
        fillcolor="rgba(42, 120, 214, 0.08)",
        name="Corridas",
        hovertemplate=f"%{{x|{fmt}}}<br>%{{y:,.0f}} corridas<extra></extra>",
    )
    fig.update_yaxes(rangemode="tozero", tickformat=",.0f")
    fig.update_xaxes(tickformat="%d/%m", showspikes=True, spikecolor=theme.MUTED, spikethickness=1)
    return fig


def heatmap_figure(rows: list[dict], days: list[date]):
    """Média de corridas por dia em cada hora × dia da semana (vazio se o dia não ocorre)."""
    if not rows:
        return theme.empty()
    count = {dow: sum(d.isoweekday() == dow for d in days) for dow in range(1, 8)}
    totals = {(r["dow"], r["hour"]): r["trips"] for r in rows}
    z = [
        [totals.get((dow, h), 0) / count[dow] if count[dow] else None for h in range(24)]
        for dow in range(1, 8)
    ]
    fig = theme.figure()
    fig.add_heatmap(
        z=z,
        x=[f"{h:02d}h" for h in range(24)],
        y=WEEKDAYS,
        colorscale=[[i / (len(theme.SEQUENTIAL) - 1), c] for i, c in enumerate(theme.SEQUENTIAL)],
        xgap=2,
        ygap=2,
        colorbar={"thickness": 10, "tickformat": ",.0f", "outlinewidth": 0},
        hovertemplate="%{y}, %{x}: %{z:,.0f} corridas por dia<extra></extra>",
    )
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showgrid=False, dtick=2)
    return fig


def ranking_figure(rows: list[dict]):
    if not rows:
        return theme.empty()
    positions = list(
        range(len(rows))
    )  # posição numérica: zonas homônimas (ex.: Corona) não colidem
    fig = theme.figure(margin={"l": 8, "r": 90, "t": 8, "b": 8}, bargap=0.25)
    fig.add_bar(
        x=[r["trips"] for r in rows],
        y=positions,
        orientation="h",
        marker={"color": theme.BLUE, "cornerradius": 4},
        text=[f"{theme.number(r['trips'])} · {theme.number(100 * r['share'], 1)}%" for r in rows],
        textposition="outside",
        textfont={"color": theme.INK},
        cliponaxis=False,
        customdata=[[r["zone"], r["borough"], 100 * r["share"]] for r in rows],
        hovertemplate=(
            "<b>%{customdata[0]}</b> (%{customdata[1]})<br>%{x:,.0f} corridas · "
            "%{customdata[2]:,.1f}% do total<extra></extra>"
        ),
    )
    fig.update_yaxes(
        tickvals=positions,
        ticktext=[r["zone"] for r in rows],
        autorange="reversed",
        showgrid=False,
        tickfont={"color": theme.INK},
    )
    fig.update_xaxes(visible=False)
    return fig


# --- Página ------------------------------------------------------------------------------


def _chart(title: str, graph_id: str, note: str, height: int, wide: bool = False):
    return html.Div(
        [
            html.H3(title),
            html.P(note, className="chart-note"),
            dcc.Loading(
                dcc.Graph(
                    id=graph_id,
                    config={"displayModeBar": False, "responsive": True},
                    style={"height": f"{height}px"},
                ),
                type="dot",
                color=theme.BLUE,
            ),
        ],
        className="card chart" + (" wide" if wide else ""),
    )


def layout() -> list:
    return [
        html.Div(id="m-kpis", className="kpis"),
        html.Div(
            className="charts",
            children=[
                _chart(
                    "Demanda ao longo do tempo",
                    "m-trend",
                    f"Corridas por hora (até {HOURLY_MAX_DAYS} dias) ou por dia.",
                    300,
                    wide=True,
                ),
                _chart(
                    "Horário e dia da semana",
                    "m-heatmap",
                    "Média de corridas por dia, em cada hora local de embarque.",
                    380,
                ),
                _chart(
                    f"Top {TOP_ZONES} zonas de embarque",
                    "m-ranking",
                    "Corridas e participação no total filtrado.",
                    380,
                ),
            ],
        ),
    ]


@callback(
    Output("m-kpis", "children"),
    Output("m-trend", "figure"),
    Output("m-heatmap", "figure"),
    Output("m-ranking", "figure"),
    Input("f-period", "start_date"),
    Input("f-period", "end_date"),
    Input("f-borough", "value"),
    Input("f-zones", "value"),
    Input("f-service", "value"),
)
def update(start, end, borough, zones, services):
    if not start or not end:
        return [], theme.empty(), theme.empty(), theme.empty()
    p = params(start, end, borough, zones, services)
    days = days_in(p)
    grain = "hour" if len(days) <= HOURLY_MAX_DAYS else "day"
    return (
        kpi_tiles(load_kpis(p), len(days)),
        trend_figure(load_trend(p, grain), grain),
        heatmap_figure(load_heatmap(p), days),
        ranking_figure(load_ranking(p)),
    )
