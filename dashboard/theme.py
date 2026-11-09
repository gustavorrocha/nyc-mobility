"""Estilo comum dos gráficos e formatação de números em pt-BR."""

import plotly.graph_objects as go

# Paleta de referência (skill dataviz): azul para série única, rampa sequencial azul.
BLUE = "#2a78d6"
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
INK, MUTED, GRID = "#1b2430", "#5d6b7a", "#e6eaef"
FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'


def figure(**layout) -> go.Figure:
    """Figura vazia com o estilo do app (separadores pt-BR, eixos discretos)."""
    axis = {"gridcolor": GRID, "linecolor": GRID, "zeroline": False, "tickfont": {"color": MUTED}}
    fig = go.Figure()
    fig.update_layout(
        separators=",.",
        font={"family": FONT, "color": INK, "size": 12},
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel={"bgcolor": "#fff", "bordercolor": GRID, "font": {"color": INK}},
        xaxis=axis,
        yaxis=axis,
        showlegend=False,
    )
    fig.update_layout(**layout)
    return fig


def empty(message: str = "Sem dados para os filtros selecionados.") -> go.Figure:
    fig = figure(xaxis={"visible": False}, yaxis={"visible": False})
    fig.add_annotation(text=message, showarrow=False, font={"color": MUTED, "size": 14})
    return fig


def number(value, decimals: int = 0) -> str:
    """1234567.8 -> '1.234.567,8'; None -> '—'."""
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".translate(str.maketrans(",.", ".,"))
