"""Comparações entre modalidades, sobre os mesmos filtros de mobilidade."""

from dash import Input, Output, callback, html

from dashboard import coverage, mobility, theme
from dashboard.db import query

SERVICES = {
    "yellow": ("Yellow Taxi", theme.BLUE, "solid"),
    "green": ("Green Taxi", "#18834b", "dash"),
    "fhv": ("FHV", "#ab6337", "dot"),
    "fhvhv": ("HVFHV", "#7558a5", "dashdot"),
}
METRICS = {
    "trips": ("Corridas", 0, ""),
    "duration_min": ("Duração média", 1, "min"),
    "distance_mi": ("Distância média", 2, "mi"),
    "speed_mph": ("Velocidade média", 1, "mph"),
    "amount_per_trip": ("Valor médio", 2, "US$"),
}


def load_services(p: dict) -> list[dict]:
    return query(
        f"""WITH demand AS (
            SELECT d.service_type, sum(d.trip_count)::bigint AS trips,
                   sum(d.measured_trip_count)::bigint AS measured,
                   sum(d.duration_min_sum) / nullif(sum(d.measured_trip_count), 0) AS duration_min,
                   sum(d.distance_miles_sum) / nullif(sum(d.measured_trip_count), 0) AS distance_mi,
                   sum(d.distance_miles_sum) / nullif(sum(d.duration_min_sum) / 60, 0) AS speed_mph,
                   sum(d.amount_sum) / nullif(sum(d.measured_trip_count), 0) AS amount_per_trip
            FROM {mobility.SOURCE} WHERE {mobility.WHERE} GROUP BY 1
        )
        SELECT s.service_type,
               CASE WHEN EXISTS ({coverage.COVERED_PERIODS})
                    THEN coalesce(d.trips, 0) END AS trips,
               d.measured, d.duration_min, d.distance_mi, d.speed_mph, d.amount_per_trip
        FROM unnest(%(services)s::text[]) AS s(service_type)
        LEFT JOIN demand AS d USING (service_type)
        ORDER BY array_position(%(services)s::text[], s.service_type)""",
        p,
    )


def load_service_trend(p: dict, grain: str) -> list[dict]:
    return query(
        f"""WITH demand AS (
            SELECT date_trunc(%(grain)s, d.hour_local) AS t, d.service_type,
                   sum(d.trip_count)::bigint AS trips
            FROM {mobility.SOURCE} WHERE {mobility.WHERE} GROUP BY 1, 2
        )
        SELECT calendar.t, s.service_type,
               CASE WHEN to_char(calendar.t, 'YYYY-MM') IN ({coverage.COVERED_PERIODS})
                    THEN coalesce(d.trips, 0) END AS trips
        FROM generate_series(%(start)s::timestamp,
             %(end)s::timestamp - %(step)s::interval, %(step)s::interval) AS calendar(t)
        CROSS JOIN unnest(%(services)s::text[]) AS s(service_type)
        LEFT JOIN demand AS d ON d.t = calendar.t AND d.service_type = s.service_type
        ORDER BY array_position(%(services)s::text[], s.service_type), calendar.t""",
        {**p, "grain": grain, "step": f"1 {grain}"},
    )


def format_metric(key: str, value) -> str:
    if value is None:
        return "—"
    _, decimals, unit = METRICS[key]
    number = theme.number(value, decimals)
    return f"US$ {number}" if unit == "US$" else f"{number} {unit}".rstrip()


def service_trend_figure(rows: list[dict], grain: str):
    if not any(r["trips"] is not None for r in rows):
        return theme.empty("Sem cobertura comum às modalidades selecionadas.")
    fig = theme.figure(
        showlegend=True, hovermode="x unified", legend={"orientation": "h", "y": -0.2}
    )
    fmt = "%d/%m/%Y %Hh" if grain == "hour" else "%d/%m/%Y"
    for service in dict.fromkeys(r["service_type"] for r in rows):
        group = [r for r in rows if r["service_type"] == service]
        label, color, dash = SERVICES.get(service, (service, theme.MUTED, "solid"))
        fig.add_scatter(
            x=[r["t"] for r in group],
            y=[r["trips"] for r in group],
            mode="lines",
            name=label,
            connectgaps=False,
            line={"color": color, "dash": dash, "width": 2},
            hovertemplate=f"%{{x|{fmt}}}<br>%{{y:,.0f}} corridas<extra>{label}</extra>",
        )
    fig.update_yaxes(rangemode="tozero", tickformat=",.0f")
    fig.update_xaxes(tickformat="%d/%m")
    return fig


def share_figure(rows: list[dict]):
    if not any(r["trips"] is not None for r in rows):
        return theme.empty("Sem cobertura comum às modalidades selecionadas.")
    total = sum(r["trips"] or 0 for r in rows)
    if not total:
        return theme.empty("Sem corridas no período coberto para calcular participação.")
    fig = theme.figure()
    fig.add_bar(
        x=[100 * (r["trips"] or 0) / total for r in rows],
        y=[SERVICES.get(r["service_type"], (r["service_type"],))[0] for r in rows],
        orientation="h",
        customdata=[r["trips"] for r in rows],
        marker={"color": [SERVICES.get(r["service_type"], (None, theme.MUTED))[1] for r in rows]},
        hovertemplate="%{y}: %{x:,.1f}%<br>%{customdata:,.0f} corridas<extra></extra>",
    )
    fig.update_xaxes(range=[0, 100], ticksuffix="%")
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return fig


def service_rows(rows: list[dict]) -> list[dict]:
    total = sum(r["trips"] or 0 for r in rows)
    result = []
    for r in rows:
        share = (r["trips"] or 0) / total if total else None
        base = r["measured"] / r["trips"] if r["trips"] else None
        result.append(
            {
                "service": SERVICES.get(r["service_type"], (r["service_type"],))[0],
                **{key: format_metric(key, r[key]) for key in METRICS},
                "share": "—" if share is None else f"{theme.number(share * 100, 1)}%",
                "coverage": "—" if base is None else f"{theme.number(base * 100, 1)}%",
            }
        )
    return result


def table(columns: list[tuple[str, str]], rows: list[dict]):
    return html.Div(
        html.Table(
            [
                html.Thead(html.Tr([html.Th(label, scope="col") for _, label in columns])),
                html.Tbody([html.Tr([html.Td(r[key]) for key, _ in columns]) for r in rows]),
            ],
            className="data-table",
        ),
        className="table-wrap",
    )


def service_layout() -> list:
    return [
        html.H3("Comparação entre modalidades", className="comparison-title"),
        html.P(
            "Mesmos filtros e meses cobertos para todas as modalidades selecionadas. "
            "A participação considera somente essas modalidades, não todo o mercado. "
            "As médias usam a mesma base de corridas com métricas disponíveis e plausíveis. "
            "FHV sem distância/valor participa apenas da demanda e participação.",
            className="chart-note",
        ),
        html.Div(
            [
                theme.chart_card(
                    "Demanda por modalidade",
                    "s-trend",
                    "Uma série por modalidade, com lacunas onde falta cobertura.",
                    320,
                ),
                theme.chart_card(
                    "Participação nas corridas",
                    "s-share",
                    "Participação no total filtrado, no mesmo período coberto.",
                    320,
                ),
            ],
            className="charts",
        ),
        html.Div(id="s-metrics", className="card comparison-table"),
    ]


@callback(
    Output("s-trend", "figure"),
    Output("s-share", "figure"),
    Output("s-metrics", "children"),
    Input("f-period", "start_date"),
    Input("f-period", "end_date"),
    Input("f-borough", "value"),
    Input("f-zones", "value"),
    Input("f-service", "value"),
)
def update_services(start, end, borough, zones, services):
    if not start or not end or not services:
        return theme.empty(), theme.empty(), []
    p = mobility.params(start, end, borough, zones, services)
    grain = "hour" if (p["end"] - p["start"]).days <= mobility.HOURLY_MAX_DAYS else "day"
    rows = load_services(p)
    columns = [
        ("service", "Modalidade"),
        ("trips", "Corridas"),
        ("share", "Participação"),
        ("coverage", "Base das métricas"),
    ]
    columns += [(key, label) for key, (label, _, _) in METRICS.items() if key != "trips"]
    return (
        service_trend_figure(load_service_trend(p, grain), grain),
        share_figure(rows),
        table(columns, service_rows(rows)),
    )
