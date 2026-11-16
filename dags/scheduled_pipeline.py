"""Verificação diária das fontes; backfill limitado pela mesma CLI usada manualmente."""

import json
import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta

from airflow.sdk import DAG, Param, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"

with DAG(
    dag_id="scheduled_pipeline",
    description="Descobre publicações e processa competências pendentes, sem duplicar dados",
    schedule="0 9 * * *",  # diariamente às 09h UTC (06h de Brasília)
    start_date=datetime(2025, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    params={
        "start_period": Param(
            os.getenv("NYC_MOBILITY_START_PERIOD", "2025-01"), type="string", pattern=PERIOD_PATTERN
        ),
        "end_period": Param(
            os.getenv("NYC_MOBILITY_END_PERIOD", "") or None,
            type=["null", "string"],
            pattern=PERIOD_PATTERN,
            description="Vazio: até o último mês completo",
        ),
        "max_periods": Param(1, type="integer", minimum=1),
        "include_hvfhv": Param(False, type="boolean"),
    },
    tags=["nyc-mobility"],
) as dag:

    @task(
        retries=2,
        retry_delay=timedelta(minutes=5),
        retry_exponential_backoff=True,
        execution_timeout=timedelta(hours=6),
    )
    def sync():
        params = get_current_context()["params"]
        cmd = [
            "nyc-mobility",
            "sync",
            "--start-period",
            params["start_period"],
            "--max-periods",
            str(params["max_periods"]),
        ]
        if params["end_period"]:
            cmd += ["--end-period", params["end_period"]]
        if params["include_hvfhv"]:
            cmd.append("--include-hvfhv")
        result = subprocess.run(cmd, capture_output=True, text=True)
        logging.getLogger(__name__).info("%s\n%s", result.stdout, result.stderr)
        if result.returncode in (2, 3, 4):
            raise AirflowFailException(
                result.stderr.strip()
                or f"nyc-mobility sync terminou com código {result.returncode}"
            )
        if result.returncode:
            raise RuntimeError(f"nyc-mobility sync terminou com código {result.returncode}")
        return json.loads(result.stdout.strip().splitlines()[-1])

    sync()
