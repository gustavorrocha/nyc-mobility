"""Fluxo comum de ingestão idempotente e transacional, delegando a carga a cada adaptador.

Um adaptador é um módulo com SOURCE, DATASET, PERIODIC, `url(period)`, `load(...)` e,
opcionalmente, `landing_name(period)` e `fingerprint(path)`. `load` valida o arquivo e substitui
a versão publicada em raw sem fazer commit; o commit (dados + status da execução) é feito aqui.
"""

import json

import sqlalchemy as sa

from nyc_mobility import landing, openmeteo, tlc_yellow, tlc_zones
from nyc_mobility.common import period_bounds
from nyc_mobility.config import database_url, landing_dir

ADAPTERS = {f"{m.SOURCE}.{m.DATASET}": m for m in (tlc_yellow, tlc_zones, openmeteo)}


class ConcurrentLoadError(RuntimeError):
    """Outra carga da mesma fonte/competência está em andamento."""


def ingest(
    dataset: str, period: str | None = None, *, force: bool = False, url: str | None = None
) -> dict:
    """Ingere `dataset` (ex.: tlc.yellow) na competência `period`. `url` só muda em testes."""
    adapter = ADAPTERS[dataset]
    if adapter.PERIODIC:
        period_bounds(period or "")
    elif period is not None:
        raise ValueError(f"{dataset} é uma referência sem competência; não informe period")
    root = landing_dir()
    dest = root / adapter.SOURCE / adapter.DATASET / (period or "")
    landed = landing.download(
        url or adapter.url(period),
        dest,
        name=adapter.landing_name(period) if hasattr(adapter, "landing_name") else None,
        fingerprint=getattr(adapter, "fingerprint", None),
    )
    engine = sa.create_engine(database_url(), poolclass=sa.pool.NullPool)
    raw = engine.raw_connection()
    try:
        return _ingest(raw.driver_connection, adapter, dataset, period, landed, root, force)
    finally:
        raw.close()  # encerra a sessão e libera o advisory lock


def _ingest(pg, adapter, dataset, period, landed, root, force) -> dict:
    cur = pg.cursor()
    cur.execute(
        """INSERT INTO metadata.sources (source, dataset) VALUES (%s, %s)
           ON CONFLICT (source, dataset) DO UPDATE SET source = EXCLUDED.source
           RETURNING source_id""",
        (adapter.SOURCE, adapter.DATASET),
    )
    source_id = cur.fetchone()[0]
    pg.commit()

    # Lock de sessão por fonte/competência (0 = referência sem competência): protege também
    # execuções disparadas fora do Airflow.
    lock_key = int(period.replace("-", "")) if period else 0
    cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (source_id, lock_key))
    if not cur.fetchone()[0]:
        raise ConcurrentLoadError(f"Carga de {dataset} {period or ''} já em andamento")

    cur.execute(
        """INSERT INTO metadata.source_files
               (source_id, period, sha256, landing_path, size_bytes, acquisition_params)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (source_id, period, sha256) DO UPDATE SET sha256 = EXCLUDED.sha256
           RETURNING source_file_id""",
        (
            source_id,
            period,
            landed.sha256,
            landed.path.relative_to(root).as_posix(),
            landed.size_bytes,
            json.dumps(landed.acquisition_params),
        ),
    )
    source_file_id = cur.fetchone()[0]
    pg.commit()
    summary = {
        "dataset": dataset,
        "period": period,
        "source_file_id": source_file_id,
        "sha256": landed.sha256,
    }

    cur.execute(
        """SELECT source_file_id FROM metadata.ingestion_runs
           WHERE source_id = %s AND period IS NOT DISTINCT FROM %s AND status = 'succeeded'
           ORDER BY run_id DESC LIMIT 1""",
        (source_id, period),
    )
    published = cur.fetchone()
    if not force and published and published[0] == source_file_id:
        cur.execute(
            """INSERT INTO metadata.ingestion_runs
                   (source_id, period, source_file_id, status, finished_at, details)
               VALUES (%s, %s, %s, 'skipped', now(), %s) RETURNING run_id""",
            (
                source_id,
                period,
                source_file_id,
                json.dumps({"reason": "versão (hash) já publicada"}),
            ),
        )
        summary.update(run_id=cur.fetchone()[0], status="skipped")
        pg.commit()
        return summary

    cur.execute(
        """INSERT INTO metadata.ingestion_runs
               (source_id, period, source_file_id, status, details)
           VALUES (%s, %s, %s, 'running', %s) RETURNING run_id""",
        (source_id, period, source_file_id, json.dumps({"force": force})),
    )
    run_id = cur.fetchone()[0]
    pg.commit()
    try:
        stats = adapter.load(pg, landed.path, period, source_file_id)
        cur.execute(
            """UPDATE metadata.ingestion_runs SET status = 'succeeded', finished_at = now(),
                   rows_read = %s, rows_inserted = %s, rows_flagged = %s,
                   details = details || %s::jsonb
               WHERE run_id = %s""",
            (
                stats["rows_read"],
                stats["rows_inserted"],
                stats["rows_flagged"],
                json.dumps(stats["details"]),
                run_id,
            ),
        )
        pg.commit()  # dados publicados e status da execução na mesma transação
    except Exception as exc:
        pg.rollback()  # a versão publicada em raw permanece intacta
        cur.execute(
            """UPDATE metadata.ingestion_runs SET status = 'failed', finished_at = now(),
                   error = %s WHERE run_id = %s""",
            (f"{type(exc).__name__}: {exc}", run_id),
        )
        pg.commit()
        raise
    return {**summary, "run_id": run_id, "status": "succeeded", **stats}
