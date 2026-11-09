"""Pipeline mensal: ingestão das fontes da competência e `dbt build`.

A DAG só orquestra: a lógica fica na CLI `nyc-mobility` e no projeto dbt, executados em
processos próprios. Entre tarefas trafegam apenas os resumos JSON das ingestões (XCom).
"""

import json
import logging
import subprocess
from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
# Código de saída da CLI para arquivo fora do contrato (nyc_mobility.cli.EXIT_CONTRACT_ERROR).
EXIT_CONTRACT_ERROR = 3

log = logging.getLogger(__name__)

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

    @task
    def ingest(dataset: str, periodic: bool = True) -> dict:
        """Chama a CLI em processo próprio, com argumentos em lista (sem shell)."""
        params = get_current_context()["params"]
        cmd = ["nyc-mobility", "ingest", "--dataset", dataset]
        if periodic:
            cmd += ["--period", params["period"]]
            if params["force"]:
                cmd.append("--force")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        log.info("%s\n%s%s", " ".join(cmd), proc.stdout, proc.stderr)
        if proc.returncode == EXIT_CONTRACT_ERROR:
            # Arquivo fora do contrato: falha sem retry (repetir baixaria o mesmo arquivo).
            raise AirflowFailException(proc.stderr.strip().splitlines()[-1])
        if proc.returncode:
            raise RuntimeError(f"nyc-mobility terminou com código {proc.returncode}")
        # A CLI imprime o resumo da execução como JSON na última linha.
        return json.loads(proc.stdout.strip().splitlines()[-1])

    # Referência versionada: sem competência e sem --force (forçar reprocessaria todas as viagens).
    zones = ingest.override(task_id="tlc_taxi_zones")("tlc.taxi_zones", periodic=False)
    yellow = ingest.override(task_id="tlc_yellow")("tlc.yellow")
    weather = ingest.override(task_id="openmeteo_era5")("openmeteo.era5")

    # Só roda com as três fontes concluídas (succeeded ou skipped). Sem retry: falhas de modelo
    # ou de teste são determinísticas; reexecutar só o dbt = "Clear" nesta tarefa.
    dbt_build = BashOperator(task_id="dbt_build", bash_command="dbt build", retries=0)

    @task
    def summary(*results: dict) -> dict:
        """Resultado consolidado da execução (também em metadata.ingestion_runs)."""
        keys = ("status", "reason", "run_id", "source_file_id", "rows_inserted", "rows_flagged")
        report = {r["dataset"]: {k: r.get(k) for k in keys} for r in results}
        log.info("Resumo: %s", json.dumps(report, ensure_ascii=False))
        return report

    [zones, yellow, weather] >> dbt_build >> summary(zones, yellow, weather)
