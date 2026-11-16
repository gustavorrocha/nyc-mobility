"""dbt build rastreado, comum à execução manual e ao Airflow."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import sqlalchemy as sa
from psycopg.types.json import Jsonb

from nyc_mobility.common import period_bounds
from nyc_mobility.config import database_url

INPUT_VERSIONS = """
    SELECT coalesce(jsonb_object_agg(key, source_file_id), '{}') FROM (
        SELECT DISTINCT ON (r.source_id, r.period)
            s.source || '.' || s.dataset || '@' || coalesce(r.period, 'reference') AS key,
            r.source_file_id
        FROM metadata.ingestion_runs AS r
        JOIN metadata.sources AS s USING (source_id)
        WHERE r.status = 'succeeded'
        ORDER BY r.source_id, r.period, r.run_id DESC
    ) AS inputs
"""


def read_results(path: Path) -> list[dict]:
    """Guarda só os campos operacionais do artefato, sem SQL compilado ou argumentos."""
    results = json.loads(path.read_text(encoding="utf-8"))["results"]
    if not isinstance(results, list) or not results:
        raise ValueError("dbt não produziu resultados de modelos/testes")
    fields = ("unique_id", "status", "message", "execution_time", "failures")
    if any(
        not isinstance(r, dict) or not r.get("unique_id") or not r.get("status") for r in results
    ):
        raise ValueError("Artefato dbt inválido: resultado sem identificação ou status")
    return [{key: r.get(key) for key in fields} for r in results]


def build(period: str | None = None, *, full_refresh: bool = False) -> dict:
    """Executa o projeto inteiro; period identifica a solicitação, não restringe o dbt.

    Cada chamada usa target próprio para nunca ler um run_results.json de uma execução anterior.
    O lock cobre chamadas desta CLI; dbt invocado diretamente não participa do rastreamento.
    """
    if period is not None:
        period_bounds(period)
    project = Path(os.environ.get("DBT_PROJECT_DIR", Path(__file__).resolve().parents[2] / "dbt"))
    profiles = Path(os.environ.get("DBT_PROFILES_DIR", project))
    engine = sa.create_engine(database_url(), poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            locked = conn.scalar(
                sa.text("SELECT pg_try_advisory_lock(hashtextextended('nyc-mobility:dbt', 0))")
            )
            if not locked:
                raise RuntimeError("Já existe um dbt build rastreado em execução")
            run_id = conn.scalar(
                sa.text(f"""
                    INSERT INTO metadata.analytics_runs (period, status, input_versions)
                    VALUES (:period, 'running', ({INPUT_VERSIONS})) RETURNING run_id
            """),
                {"period": period},
            )
            conn.commit()  # publica running antes de iniciar o subprocesso; lock é de sessão
            results, error, returncode = [], None, 1
            try:
                with TemporaryDirectory(prefix="nyc-mobility-dbt-") as target:
                    cmd = [
                        shutil.which("dbt") or "dbt",
                        "build",
                        "--project-dir",
                        str(project),
                        "--profiles-dir",
                        str(profiles),
                        "--target-path",
                        target,
                    ]
                    if full_refresh:
                        cmd.append("--full-refresh")
                    returncode = subprocess.run(cmd, check=False).returncode
                    artifact = Path(target) / "run_results.json"
                    if artifact.exists():
                        results = read_results(artifact)
                    else:
                        error = "dbt terminou sem produzir run_results.json; consulte os logs"
                    if returncode or any(
                        r["status"] not in {"success", "pass", "warn"} for r in results
                    ):
                        error = (
                            error
                            or f"dbt build falhou (código {returncode}); consulte os resultados"
                        )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            except BaseException as exc:
                _finish(conn, run_id, results, f"Execução interrompida: {type(exc).__name__}")
                raise
            error = _finish(conn, run_id, results, error)
            return {
                "run_id": run_id,
                "status": "failed" if error else "succeeded",
                "returncode": returncode or (1 if error else 0),
            }
    finally:
        engine.dispose()  # NullPool fecha a sessão e libera o advisory lock


def _finish(conn, run_id: int, results: list[dict], error: str | None) -> str | None:
    if error is None:
        original = conn.scalar(
            sa.text("SELECT input_versions FROM metadata.analytics_runs WHERE run_id = :id"),
            {"id": run_id},
        )
        if original != conn.scalar(sa.text(INPUT_VERSIONS)):
            error = "Os insumos mudaram durante o build; reexecute para validar a nova versão"
    periods = []
    if error is None and conn.scalar(sa.text("SELECT to_regclass('analytics.data_coverage')")):
        periods = list(
            conn.scalars(
                sa.text(
                    "SELECT DISTINCT period FROM analytics.data_coverage "
                    "WHERE status = 'available' "
                    "ORDER BY period"
                )
            )
        )
    conn.execute(
        sa.text("""
        UPDATE metadata.analytics_runs SET status = :status, finished_at = clock_timestamp(),
            node_results = :results, error = :error,
            validated_periods = :periods
        WHERE run_id = :id
    """),
        {
            "id": run_id,
            "status": "failed" if error else "succeeded",
            "results": Jsonb(results),
            "error": error,
            "periods": periods,
        },
    )
    conn.commit()
    return error
