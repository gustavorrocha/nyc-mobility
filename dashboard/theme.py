"""Estilo comum dos gráficos e formatação de números em pt-BR."""

import plotly.graph_objects as go
from dash import dcc, html

# Paleta de referência (skill dataviz): azul para série única, rampa sequencial azul.
BLUE = "#2a78d6"
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
INK, MUTED, GRID = "#1b2430", "#5d6b7a", "#e6eaef"
FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'


def style(fig: go.Figure, **layout) -> go.Figure:
    """Estilo do app (separadores pt-BR, eixos discretos) em todos os eixos, inclusive subplots."""
    fig.update_layout(
        separators=",.",
        font={"family": FONT, "color": INK, "size": 12},
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel={"bgcolor": "#fff", "bordercolor": GRID, "font": {"color": INK}},
        showlegend=False,
    )
    axis = {"gridcolor": GRID, "linecolor": GRID, "zeroline": False, "tickfont": {"color": MUTED}}
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis, title_font={"color": MUTED, "size": 11})
    fig.update_layout(**layout)
    return fig


def figure(**layout) -> go.Figure:
    return style(go.Figure(), **layout)


def empty(message: str = "Sem dados para os filtros selecionados.") -> go.Figure:
    fig = figure(xaxis={"visible": False}, yaxis={"visible": False})
    fig.add_annotation(text=message, showarrow=False, font={"color": MUTED, "size": 14})
    return fig


def number(value, decimals: int = 0) -> str:
    """1234567.8 -> '1.234.567,8'; None -> '—'."""
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".translate(str.maketrans(",.", ".,"))


def percent(value) -> str:
    """0.123 -> '+12,3%'; None -> '—'."""
    if value is None:
        return "—"
    return f"{'+' if value > 0 else ''}{number(100 * value, 1)}%"


def kpi_cards(tiles: list[tuple[str, str, str]]) -> list:
    """(rótulo, valor, nota) -> cartões de KPI."""
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


def chart_card(title: str, graph_id: str, note: str, height: int, wide: bool = False):
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
                color=BLUE,
            ),
        ],
        className="card chart" + (" wide" if wide else ""),
    )
