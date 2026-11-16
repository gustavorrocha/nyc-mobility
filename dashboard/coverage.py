"""Cobertura mensal compartilhada pelas páginas de mobilidade e clima."""

from datetime import timedelta

from dash import html

from dashboard.db import query

LABELS = {
    "available": "Disponível",
    "pending": "Aguardando atualização analítica",
    "missing": "Sem carga",
}

# Comparações e totais usam somente meses cobertos em todas as modalidades selecionadas.
COVERED_PERIODS = """
    SELECT period FROM analytics.data_coverage
    WHERE service_type = ANY(%(services)s)
      AND period >= to_char(%(start)s::date, 'YYYY-MM')
      AND period <= to_char(%(end)s::date - 1, 'YYYY-MM')
    GROUP BY period
    HAVING count(DISTINCT service_type) = cardinality(%(services)s::text[])
       AND bool_and(status = 'available')
"""
WEATHER_PERIODS = COVERED_PERIODS.replace(
    "bool_and(status = 'available')",
    "bool_and(status = 'available' AND weather_status = 'available')",
)


def load(p: dict, *, weather: bool = False) -> list[dict]:
    return query(
        """SELECT to_char(m, 'YYYY-MM') AS period, s AS service_type,
                  CASE WHEN %(weather)s AND c.status = 'available'
                       THEN c.weather_status ELSE coalesce(c.status, 'missing') END AS status
           FROM generate_series(date_trunc('month', %(start)s::timestamp),
               date_trunc('month', %(end)s::timestamp - interval '1 day'),
               interval '1 month') AS m
           CROSS JOIN unnest(%(services)s::text[]) AS s
           LEFT JOIN analytics.data_coverage AS c
               ON c.period = to_char(m, 'YYYY-MM') AND c.service_type = s
           ORDER BY 1, 2""",
        {**p, "weather": weather},
    )


def available_periods(rows: list[dict]) -> set[str]:
    periods = {r["period"] for r in rows}
    return periods - {r["period"] for r in rows if r["status"] != "available"}


def covered_days(p: dict, rows: list[dict]):
    available = available_periods(rows)
    days = [p["start"] + timedelta(days=i) for i in range((p["end"] - p["start"]).days)]
    return [d for d in days if d.strftime("%Y-%m") in available]


def notice(rows: list[dict]):
    available = len(available_periods(rows))
    total = len({r["period"] for r in rows})
    return html.Details(
        [
            html.Summary(
                f"Cobertura: {available} de {total} meses disponíveis. "
                "Os indicadores usam apenas meses cobertos em todas as modalidades selecionadas."
                if rows
                else "Selecione uma modalidade para consultar a cobertura."
            ),
            html.Ul(
                [
                    html.Li(f"{r['period']} · {r['service_type']}: {LABELS[r['status']]}")
                    for r in rows
                ]
            ),
        ],
        className="card coverage",
        role="status",
    )
