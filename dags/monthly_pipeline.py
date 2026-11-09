"""Pipeline mensal: ingestão das fontes da competência e `dbt build`.

A DAG só orquestra: a lógica fica na CLI `nyc-mobility` e no projeto dbt, executados em
processos próprios. Entre tarefas trafegam apenas os resumos JSON das ingestões (XCom).
"""

import json
import logging
from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param, task

PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"

# Competência e --force chegam por variável de ambiente, nunca interpolados no comando.
INGEST_ENV = {
    "PERIOD": "{{ params.period }}",
    "FORCE_FLAG": "{{ '--force' if params.force else '' }}",
}

with DAG(
    dag_id="monthly_pipeline",
    description="Ingestão (Taxi Zones, Yellow, Open-Meteo) e dbt build de uma competência",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    params={
        "period": Param(type="string", pattern=PERIOD_PATTERN, description="Competência AAAA-MM"),
        "force": Param(
            False, type="boolean", description="Recarrega Yellow e clima mesmo sem hash novo"
        ),
    },
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=2),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(minutes=30),
    },
    tags=["nyc-mobility"],
) as dag:

    def ingest(task_id: str, args: str) -> BashOperator:
        return BashOperator(
            task_id=task_id,
            bash_command=f"nyc-mobility ingest {args}",
            env=INGEST_ENV,
            append_env=True,
            # A CLI imprime o resumo da execução como JSON na última linha.
            output_processor=json.loads,
        )

    # Referência versionada: sem competência e sem --force (forçar reprocessaria todas as viagens).
    zones = ingest("tlc_taxi_zones", "--dataset tlc.taxi_zones")
    yellow = ingest("tlc_yellow", '--dataset tlc.yellow --period "$PERIOD" $FORCE_FLAG')
    weather = ingest("openmeteo_era5", '--dataset openmeteo.era5 --period "$PERIOD" $FORCE_FLAG')

    # Só roda com as três fontes concluídas (succeeded ou skipped). Sem retry: falhas de modelo
    # ou de teste são determinísticas; reexecutar só o dbt = "Clear" nesta tarefa.
    dbt_build = BashOperator(task_id="dbt_build", bash_command="dbt build", retries=0)

    @task
    def summary(*results: dict) -> dict:
        """Resultado consolidado da execução (também em metadata.ingestion_runs)."""
        keys = ("status", "run_id", "source_file_id", "rows_inserted", "rows_flagged")
        report = {r["dataset"]: {k: r.get(k) for k in keys} for r in results}
        logging.getLogger(__name__).info("Resumo: %s", json.dumps(report, ensure_ascii=False))
        return report

    [zones, yellow, weather] >> dbt_build >> summary(zones.output, yellow.output, weather.output)
