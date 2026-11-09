"""Página 3 — Pipeline Health: execuções das ingestões, volumes, conciliação e qualidade.

Fontes: metadata.ingestion_runs/sources (execuções), analytics.trip_quality (corridas por
situação de qualidade), analytics.trip_demand_hourly e trip_weather_hourly (o que chegou ao
consumo). Não usa os filtros globais (são de dados de mobilidade, não de metadados).
"""

from datetime import UTC, datetime

from dash import Input, Output, callback, html

from dashboard import theme
from dashboard.db import query

# Paleta de status (skill dataviz): sempre com rótulo e símbolo, nunca só a cor.
STATUS = {
    "succeeded": ("Sucesso", "✓", "#0ca30c", "circle"),
    "skipped": ("Sem mudança", "↷", "#8a96a3", "circle-open"),
    "running": ("Em execução", "…", "#fab219", "diamond"),
    "failed": ("Falha", "✕", "#d03b3b", "x"),
}
QUALITY = {
    "invalid": ("Inválida (fora de todas as métricas)", "#d03b3b"),
    "metric_issue": ("Métrica implausível (fora das médias)", "#ec835a"),
}
REASONS = {
    "missing_required_value": "Campo obrigatório ausente",
    "nonexistent_local_time": "Horário local inexistente (DST)",
    "dropoff_before_pickup": "Desembarque antes do embarque",
    "negative_distance": "Distância negativa",
    "pickup_outside_period": "Embarque fora da competência",
    "unknown_zone": "Zona fora do cadastro",
    "implausible_duration": "Duração implausível",
    "implausible_distance": "Distância implausível",
    "implausible_speed": "Velocidade implausível",
    "implausible_amount": "Valor implausível",
}


# --- Consultas ---------------------------------------------------------------------------


def load_runs() -> list[dict]:
    return query(
        """SELECT r.run_id, s.source || '.' || s.dataset AS dataset, r.period, r.status,
                  r.started_at, r.finished_at,
                  extract(epoch FROM r.finished_at - r.started_at)::float8 AS seconds,
                  r.rows_read, r.rows_inserted, r.rows_flagged,
                  coalesce(r.error, r.details ->> 'reason') AS message
           FROM metadata.ingestion_runs AS r
           JOIN metadata.sources AS s USING (source_id)
           ORDER BY r.started_at DESC, r.run_id DESC
           LIMIT 1000"""
    )


def load_quality() -> list[dict]:
    return query(
        """SELECT period, status, reason, sum(trip_count)::bigint AS trips
           FROM analytics.trip_quality GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""
    )


def load_consumption() -> list[dict]:
    """Por competência: o que chegou ao analytics (corridas e cobertura de horas de clima)."""
    return query(
        """WITH demand AS (
               SELECT period, sum(trip_count)::bigint AS valid,
                      sum(measured_trip_count)::bigint AS measured
               FROM analytics.trip_demand_hourly GROUP BY 1
           ),
           weather AS (
               SELECT period, count(*) AS weather_hours,
                      count(precipitation_mm) AS precipitation_hours,
                      (extract(epoch FROM
                          (to_date(period, 'YYYY-MM') + interval '1 month')::timestamp
                              AT TIME ZONE 'America/New_York'
                          - to_date(period, 'YYYY-MM')::timestamp
                              AT TIME ZONE 'America/New_York') / 3600)::int AS expected_hours
               FROM analytics.trip_weather_hourly GROUP BY 1
           )
           SELECT * FROM demand FULL JOIN weather USING (period) ORDER BY period"""
    )


# --- Regras ------------------------------------------------------------------------------


def published(runs: list[dict]) -> dict[tuple[str, str | None], dict]:
    """Última execução bem-sucedida por (dataset, competência): a versão publicada em raw."""
    result = {}
    for r in sorted(runs, key=lambda r: r["started_at"]):
        if r["status"] == "succeeded":
            result[(r["dataset"], r["period"])] = r
    return result


def reconciliation(runs: list[dict], quality: list[dict], consumption: list[dict]) -> list[dict]:
    """raw (ingestão) = core (todas as corridas) e core − inválidas = analytics, por competência."""
    raw = {p: r["rows_inserted"] for (d, p), r in published(runs).items() if d == "tlc.yellow"}
    core, invalid = {}, {}
    for q in quality:
        core[q["period"]] = core.get(q["period"], 0) + q["trips"]
        if q["status"] == "invalid":
            invalid[q["period"]] = invalid.get(q["period"], 0) + q["trips"]
    used = {c["period"]: c for c in consumption}
    rows = []
    for period in sorted(set(raw) | set(core) | set(used)):
        c = used.get(period, {})
        valid = c.get("valid")
        ok = (
            raw.get(period) is not None
            and raw.get(period) == core.get(period)
            and core.get(period, 0) - invalid.get(period, 0) == (valid or 0)
        )
        rows.append(
            {
                "period": period,
                "raw": raw.get(period),
                "core": core.get(period),
                "invalid": invalid.get(period, 0),
                "valid": valid,
                "measured": c.get("measured"),
                "weather_hours": c.get("weather_hours"),
                "expected_hours": c.get("expected_hours"),
                "precipitation_hours": c.get("precipitation_hours"),
                "ok": ok,
            }
        )
    return rows


def summary(runs: list[dict], recon: list[dict], now: datetime) -> dict:
    counts = {s: sum(r["status"] == s for r in runs) for s in STATUS}
    finished = [r["finished_at"] for r in runs if r["status"] == "succeeded" and r["finished_at"]]
    valid = sum(r["valid"] or 0 for r in recon)
    return {
        "runs": len(runs),
        **counts,
        "last_success": max(finished) if finished else None,
        "age_hours": (now - max(finished)).total_seconds() / 3600 if finished else None,
        "periods": [r["period"] for r in recon if r["valid"]],
        "reconciled": bool(recon) and all(r["ok"] for r in recon),
        "measured_share": sum(r["measured"] or 0 for r in recon) / valid if valid else None,
    }


# --- Visualizações -----------------------------------------------------------------------


def _when(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%d/%m/%Y %H:%M UTC") if value else "—"


def _age(hours: float | None) -> str:
    if hours is None:
        return "sem carga concluída"
    if hours < 1:
        return "há menos de 1 h"
    return f"há {theme.number(hours)} h" if hours < 48 else f"há {theme.number(hours / 24)} dias"


def kpi_tiles(s: dict) -> list:
    failure = s["failed"] / s["runs"] if s["runs"] else None
    return theme.kpi_cards(
        [
            ("Última carga concluída", _age(s["age_hours"]), _when(s["last_success"])),
            (
                "Execuções",
                theme.number(s["runs"]),
                f"{s['succeeded']} sucesso · {s['skipped']} sem mudança · {s['failed']} falha",
            ),
            (
                "Taxa de falha",
                "—" if failure is None else f"{theme.number(100 * failure, 1)}%",
                "execuções com status failed",
            ),
            (
                "Conciliação raw → analytics",
                "✓ OK" if s["reconciled"] else "✕ Divergente",
                ", ".join(s["periods"]) or "sem competências publicadas",
            ),
            (
                "Corridas nas médias",
                "—"
                if s["measured_share"] is None
                else f"{theme.number(100 * s['measured_share'], 1)}%",
                "válidas sem métrica implausível",
            ),
        ]
    )


def timeline_figure(runs: list[dict]):
    if not runs:
        return theme.empty("Nenhuma execução registrada.")
    fig = theme.figure(showlegend=True, legend={"orientation": "h", "y": -0.25, "x": 0})
    for status, (label, icon, color, symbol) in STATUS.items():
        group = [r for r in runs if r["status"] == status]
        if not group:
            continue
        fig.add_scatter(
            x=[r["started_at"] for r in group],
            y=[r["dataset"] for r in group],
            mode="markers",
            name=f"{icon} {label}",
            marker={"color": color, "size": 12, "symbol": symbol, "line": {"width": 2}},
            customdata=[
                [
                    r["run_id"],
                    r["period"] or "—",
                    theme.number(r["rows_inserted"]),
                    r["message"] or "",
                ]
                for r in group
            ],
            hovertemplate=(
                f"<b>{icon} {label}</b> · execução %{{customdata[0]}}<br>%{{y}} · "
                "competência %{customdata[1]}<br>%{x|%d/%m/%Y %H:%M:%S} UTC<br>"
                "linhas carregadas: %{customdata[2]}<br>%{customdata[3]}<extra></extra>"
            ),
        )
    fig.update_yaxes(showgrid=False, categoryorder="category descending")
    fig.update_xaxes(tickformat="%d/%m %Hh")
    return fig


def _share(n: int, total: int) -> str:
    pct = 100 * n / total
    return "<0,01%" if 0 < pct < 0.01 else f"{theme.number(pct, 2)}%"


def quality_figure(quality: list[dict]):
    """Motivos de qualidade em % das corridas de todas as competências."""
    total = sum(q["trips"] for q in quality)
    issues = {}
    for q in quality:
        if q["status"] != "ok":
            key = (q["status"], q["reason"])
            issues[key] = issues.get(key, 0) + q["trips"]
    if not issues:
        return theme.empty("Nenhum problema de qualidade registrado.")
    ordered = sorted(issues.items(), key=lambda item: item[1])
    fig = theme.figure(
        showlegend=True,
        legend={"orientation": "h", "y": -0.15, "x": 0},
        margin={"l": 8, "r": 110, "t": 8, "b": 8},
    )
    for status, (label, color) in QUALITY.items():
        group = [(reason, n) for (s, reason), n in ordered if s == status]
        if not group:
            continue
        fig.add_bar(
            x=[100 * n / total for _, n in group],
            y=[REASONS.get(reason, reason) for reason, _ in group],
            orientation="h",
            name=label,
            marker={"color": color, "cornerradius": 4},
            text=[f"{theme.number(n)} ({_share(n, total)})" for _, n in group],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}: %{text} das corridas<extra></extra>",
        )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(showgrid=False, tickfont={"color": theme.INK}, categoryorder="trace")
    return fig


RECENT_RUNS = 20
TONE = {"succeeded": "good", "skipped": "muted", "running": "warning", "failed": "critical"}


def _table(columns: list[tuple[str, str]], rows: list[dict], left=()):
    """Tabela HTML; a chave `_tone` de cada linha colore a 1ª coluna de status (com ícone)."""

    def cell(row, key):
        tone = row.get("_tone") if key in ("status", "check") else None
        classes = " ".join(
            c for c in ("left" if key in left else "", f"tone-{tone}" if tone else "") if c
        )
        return html.Td(row[key], className=classes or None)

    return html.Div(
        html.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th(name, className="left" if key in left else None)
                            for key, name in columns
                        ]
                    )
                ),
                html.Tbody([html.Tr([cell(row, key) for key, _ in columns]) for row in rows]),
            ],
            className="data-table",
        ),
        className="table-wrap",
    )


RECON_COLUMNS = [
    ("period", "Competência"),
    ("raw", "Raw (ingestão)"),
    ("core", "Core (dbt)"),
    ("invalid", "Inválidas"),
    ("valid", "Analytics (válidas)"),
    ("measured", "Nas médias"),
    ("weather", "Horas de clima / esperadas"),
    ("check", "Conciliação"),
]
RUN_COLUMNS = [
    ("run_id", "#"),
    ("status", "Status"),
    ("dataset", "Fonte"),
    ("period", "Competência"),
    ("started", "Início"),
    ("seconds", "Duração (s)*"),
    ("read", "Lidas"),
    ("inserted", "Carregadas"),
    ("flagged", "Sinalizadas"),
    ("message", "Motivo / erro"),
]


def reconciliation_rows(recon: list[dict]) -> list[dict]:
    rows = []
    for r in recon:
        weather = f"{theme.number(r['weather_hours'])} / {theme.number(r['expected_hours'])}"
        if r["weather_hours"] and r["precipitation_hours"] != r["weather_hours"]:
            weather += f" ({r['weather_hours'] - r['precipitation_hours']} sem precipitação)"
        rows.append(
            {
                "period": r["period"],
                "raw": theme.number(r["raw"]),
                "core": theme.number(r["core"]),
                "invalid": theme.number(r["invalid"]),
                "valid": theme.number(r["valid"]),
                "measured": theme.number(r["measured"]),
                "weather": weather,
                "check": "✓ OK" if r["ok"] else "✕ Divergente",
                "_tone": "good" if r["ok"] else "critical",
            }
        )
    return rows


def _count(value) -> str:
    return "—" if value is None else theme.number(value)


def run_rows(runs: list[dict]) -> list[dict]:
    return [
        {
            "run_id": r["run_id"],
            "status": f"{STATUS[r['status']][1]} {STATUS[r['status']][0]}",
            "dataset": r["dataset"],
            "period": r["period"] or "—",
            "started": _when(r["started_at"]),
            "seconds": "—" if r["seconds"] is None else theme.number(r["seconds"], 1),
            "read": _count(r["rows_read"]),
            "inserted": _count(r["rows_inserted"]),
            "flagged": _count(r["rows_flagged"]),
            "message": r["message"] or "",
            "_tone": TONE[r["status"]],
        }
        for r in runs[:RECENT_RUNS]
    ]


# --- Página ------------------------------------------------------------------------------


def _section(title: str, note: str, body_id: str, wide: bool = True):
    return html.Div(
        [html.H3(title), html.P(note, className="chart-note"), html.Div(id=body_id)],
        className="card chart" + (" wide" if wide else ""),
    )


def layout() -> list:
    return [
        html.Div(id="p-kpis", className="kpis"),
        html.Div(
            className="charts",
            children=[
                theme.chart_card(
                    "Execuções das ingestões",
                    "p-timeline",
                    "Cada ponto é uma execução da CLI (Airflow ou manual), por fonte.",
                    260,
                    wide=True,
                ),
                _section(
                    "Conciliação por competência",
                    "Raw = linhas carregadas na versão publicada; core = todas as corridas no dbt; "
                    "analytics = válidas. OK quando raw = core e core − inválidas = analytics.",
                    "p-recon-body",
                ),
                theme.chart_card(
                    "Qualidade dos dados",
                    "p-quality",
                    "Corridas sinalizadas por motivo, em % do total de todas as competências.",
                    380,
                    wide=True,
                ),
                _section(
                    "Histórico de execuções",
                    f"As {RECENT_RUNS} mais recentes. * Duração registrada hoje é parcial "
                    "(finished_at usa o horário de início da transação de carga); correção "
                    "pendente no pipeline.",
                    "p-runs-body",
                ),
            ],
        ),
    ]


@callback(
    Output("p-kpis", "children"),
    Output("p-timeline", "figure"),
    Output("p-recon-body", "children"),
    Output("p-quality", "figure"),
    Output("p-runs-body", "children"),
    Input("url", "pathname"),
)
def update(pathname):
    runs, quality = load_runs(), load_quality()
    recon = reconciliation(runs, quality, load_consumption())
    return (
        kpi_tiles(summary(runs, recon, datetime.now(UTC))),
        timeline_figure(runs),
        _table(RECON_COLUMNS, reconciliation_rows(recon), left=("period", "check")),
        quality_figure(quality),
        _table(
            RUN_COLUMNS, run_rows(runs), left=("status", "dataset", "period", "started", "message")
        ),
    )
