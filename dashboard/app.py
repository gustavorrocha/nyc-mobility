"""NYC Mobility Explorer: cabeçalho, navegação, filtros globais e rotas das páginas."""

from datetime import UTC

import psycopg
from dash import Dash, Input, Output, State, dcc, html

from dashboard import climate, comparisons, mobility, pipeline
from dashboard.db import query, snapshot

# Rota -> (título, descrição, conteúdo da página).
PAGES = {
    "/": (
        "Mobilidade urbana",
        "Demanda, distâncias, durações, tarifas e zonas de embarque das corridas válidas.",
        lambda: [*mobility.layout(), *comparisons.service_layout(), *comparisons.period_layout()],
    ),
    "/clima": (
        "Mobilidade e clima",
        "Como chuva, neve e temperatura mudam demanda, duração, distância, velocidade e "
        "valor das corridas.",
        climate.layout,
    ),
    "/pipeline": (
        "Pipeline Health",
        "Execuções das ingestões, volumes, conciliação e qualidade dos dados.",
        pipeline.layout,
    ),
}
NO_FILTERS = {"/pipeline"}  # filtros globais são de mobilidade, não de metadados

# suppress_callback_exceptions: o conteúdo das páginas é montado por rota, fora do layout inicial
# (e o Dash não chama a função de layout, que consulta o banco, já na importação).
app = Dash(__name__, title="NYC Mobility Explorer", suppress_callback_exceptions=True)


@snapshot()
def filter_options() -> dict:
    """Valores dos filtros globais, lidos do banco a cada carregamento de página."""
    period = query(
        "SELECT min(to_date(period, 'YYYY-MM')) AS first_day, "
        "(max(to_date(period, 'YYYY-MM')) + interval '1 month - 1 day')::date AS last_day, "
        "(SELECT published_at FROM metadata.analytics_publication) AS published_at "
        "FROM serving.data_coverage"
    )[0]
    zones = query(
        "SELECT zone_id, zone, borough FROM serving.zones "
        "WHERE zone IS NOT NULL AND borough IS NOT NULL ORDER BY borough, zone"
    )
    services = query(
        "SELECT s.service_type, s.description, EXISTS (SELECT FROM serving.data_coverage c "
        "WHERE c.service_type = s.service_type AND c.status = 'available') AS available "
        "FROM serving.service_types s ORDER BY 1"
    )
    return {**period, "zones": zones, "services": services}


def _field(label: str, control) -> html.Div:
    return html.Div([html.Label(label, className="field-label"), control], className="field")


def layout() -> html.Div:
    try:
        opts = filter_options()
    except (psycopg.Error, RuntimeError) as exc:
        return html.Main(
            className="page",
            children=[
                html.H2(
                    "Aguardando primeira publicação"
                    if str(exc).startswith("Dados ainda não publicados")
                    else "Banco de dados indisponível"
                ),
                html.P(str(exc).splitlines()[0], className="lead"),
            ],
        )
    # "N/A" e "Unknown" são valores do cadastro TLC, não distritos: suas zonas (fora de NYC,
    # desconhecida) continuam no filtro de zonas com "NYC inteira".
    boroughs = sorted({z["borough"] for z in opts["zones"]} - {"N/A", "Unknown"})
    return html.Div(
        className="shell",
        children=[
            dcc.Location(id="url"),
            dcc.Store(id="zones", data=opts["zones"]),
            html.Header(
                className="topbar",
                children=[
                    html.Div(
                        [
                            html.H1("NYC Mobility Explorer"),
                            html.P("Táxis da TLC e clima horário da Open-Meteo"),
                            html.P(
                                "Dados validados em "
                                + opts["published_at"]
                                .astimezone(UTC)
                                .strftime("%d/%m/%Y %H:%M UTC")
                                if opts.get("published_at")
                                else ""
                            ),
                        ],
                        className="brand",
                    ),
                    html.Nav(
                        [
                            dcc.Link(title, href=path, id=f"nav-{i}")
                            for i, (path, (title, _, _)) in enumerate(PAGES.items())
                        ],
                        className="nav",
                    ),
                ],
            ),
            html.Section(
                id="filters",
                className="filters",
                children=[
                    _field(
                        "Período",
                        dcc.DatePickerRange(
                            id="f-period",
                            min_date_allowed=opts["first_day"],
                            max_date_allowed=opts["last_day"],
                            start_date=opts["first_day"],
                            end_date=opts["last_day"],
                            display_format="DD/MM/YYYY",
                        ),
                    ),
                    _field(
                        "Distrito",
                        dcc.Dropdown(id="f-borough", options=boroughs, placeholder="NYC inteira"),
                    ),
                    _field(
                        "Zonas de embarque",
                        dcc.Dropdown(id="f-zones", multi=True, placeholder="Todas as zonas"),
                    ),
                    _field(
                        "Modalidade",
                        dcc.Dropdown(
                            id="f-service",
                            options=[
                                {"label": s["description"], "value": s["service_type"]}
                                for s in opts["services"]
                            ],
                            value=[
                                s["service_type"]
                                for s in opts["services"]
                                if s.get("available", True)
                            ],
                            multi=True,
                            clearable=False,
                        ),
                    ),
                ],
            ),
            html.Main(id="page", className="page"),
        ],
    )


app.layout = layout  # função: filtros recarregados do banco a cada visita


@app.callback(
    Output("page", "children"),
    [Output(f"nav-{i}", "className") for i in range(len(PAGES))],
    Output("filters", "hidden"),
    Input("url", "pathname"),
)
def route(pathname):
    nav = ["nav-link active" if path == pathname else "nav-link" for path in PAGES]
    hidden = pathname in NO_FILTERS
    if pathname not in PAGES:
        not_found = [html.H2("Página não encontrada"), dcc.Link("Voltar ao início", href="/")]
        return not_found, *nav, hidden
    title, description, content = PAGES[pathname]
    return [html.H2(title), html.P(description, className="lead"), *content()], *nav, hidden


@app.callback(
    Output("f-zones", "options"),
    Output("f-zones", "value"),
    Input("f-borough", "value"),
    State("zones", "data"),
    State("f-zones", "value"),
)
def zone_options(borough, zones, selected):
    """Zonas do distrito escolhido (todas, sem distrito); descarta seleções fora dele."""
    allowed = [z for z in zones if borough in (None, z["borough"])]
    options = [
        {"label": z["zone"] if borough else f"{z['zone']} ({z['borough']})", "value": z["zone_id"]}
        for z in allowed
    ]
    ids = {z["zone_id"] for z in allowed}
    return options, [v for v in selected or [] if v in ids]
