"""Ingestão idempotente e transacional de uma competência (AAAA-MM) do TLC Yellow Taxi."""

import io
import json
import re
from datetime import datetime

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import sqlalchemy as sa

from nyc_mobility import landing, tlc_yellow
from nyc_mobility.config import database_url, landing_dir

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
BATCH_ROWS = 100_000


class ConcurrentLoadError(RuntimeError):
    """Outra carga da mesma fonte/competência está em andamento."""


def period_bounds(period: str) -> tuple[datetime, datetime]:
    if not PERIOD_RE.match(period):
        raise ValueError(f"Competência inválida (esperado AAAA-MM): {period!r}")
    year, month = int(period[:4]), int(period[5:])
    start = datetime(year, month, 1)
    return start, datetime(year + month // 12, month % 12 + 1, 1)


def ingest_yellow(period: str, *, force: bool = False, url: str | None = None) -> dict:
    """Carrega a competência em raw.tlc_yellow_trips. `url` só é sobrescrita em testes."""
    period_bounds(period)
    root = landing_dir()
    landed = landing.download(
        url or tlc_yellow.url(period), root / tlc_yellow.SOURCE / tlc_yellow.DATASET / period
    )
    engine = sa.create_engine(database_url(), poolclass=sa.pool.NullPool)
    raw = engine.raw_connection()
    try:
        return _ingest(raw.driver_connection, period, landed, root, force)
    finally:
        raw.close()  # encerra a sessão e libera o advisory lock


def _ingest(pg, period, landed, root, force) -> dict:
    cur = pg.cursor()
    cur.execute(
        """INSERT INTO metadata.sources (source, dataset) VALUES (%s, %s)
           ON CONFLICT (source, dataset) DO UPDATE SET source = EXCLUDED.source
           RETURNING source_id""",
        (tlc_yellow.SOURCE, tlc_yellow.DATASET),
    )
    source_id = cur.fetchone()[0]
    pg.commit()

    # Lock de sessão por fonte/competência: protege também execuções fora do Airflow.
    cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (source_id, int(period.replace("-", ""))))
    if not cur.fetchone()[0]:
        raise ConcurrentLoadError(f"Carga de tlc.yellow {period} já em andamento")

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
        "dataset": "tlc.yellow",
        "period": period,
        "source_file_id": source_file_id,
        "sha256": landed.sha256,
    }

    cur.execute(
        """SELECT source_file_id FROM metadata.ingestion_runs
           WHERE source_id = %s AND period = %s AND status = 'succeeded'
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
        stats = _load(pg, landed.path, period, source_file_id)
    except Exception as exc:
        pg.rollback()  # a competência publicada em raw permanece intacta
        cur.execute(
            """UPDATE metadata.ingestion_runs SET status = 'failed', finished_at = now(),
                   error = %s WHERE run_id = %s""",
            (f"{type(exc).__name__}: {exc}", run_id),
        )
        pg.commit()
        raise
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
    pg.commit()
    return {**summary, "run_id": run_id, "status": "succeeded", **stats}


def _load(pg, path, period, source_file_id) -> dict:
    """Staging temporário + substituição da competência em raw, em uma única transação."""
    parquet = pq.ParquetFile(path)
    report = tlc_yellow.validate(parquet.schema_arrow)
    src_cols = [src for src, _ in report.columns]
    raw_cols = [dst for _, dst in report.columns] + ["row_number"]
    col_list = ", ".join(raw_cols)
    cur = pg.cursor()
    cur.execute(
        f"CREATE TEMP TABLE stage ON COMMIT DROP AS "
        f"SELECT {col_list} FROM {tlc_yellow.RAW_TABLE} WITH NO DATA"
    )
    staged = 0
    with cur.copy(f"COPY stage ({col_list}) FROM STDIN (FORMAT csv)") as copy:
        for batch in parquet.iter_batches(batch_size=BATCH_ROWS, columns=src_cols):
            table = pa.Table.from_batches([batch]).select(src_cols).rename_columns(raw_cols[:-1])
            table = table.append_column(
                "row_number", pa.array(range(staged + 1, staged + 1 + len(table)), pa.int64())
            )
            buf = io.BytesIO()
            pacsv.write_csv(table, buf, pacsv.WriteOptions(include_header=False))
            copy.write(buf.getvalue())
            staged += len(table)

    rows_read = parquet.metadata.num_rows
    if staged != rows_read:
        raise RuntimeError(f"Contagem divergente: {staged} em staging, {rows_read} no arquivo")

    # Problemas por registro: contabilizados e preservados em raw, nunca descartados.
    start, end = period_bounds(period)
    rules = {
        "missing_required_value": "tpep_pickup_datetime IS NULL OR tpep_dropoff_datetime IS NULL"
        " OR trip_distance IS NULL OR pulocationid IS NULL OR dolocationid IS NULL",
        "dropoff_before_pickup": "tpep_dropoff_datetime < tpep_pickup_datetime",
        "negative_distance": "trip_distance < 0",
        "pickup_outside_period": "NOT (tpep_pickup_datetime >= %(start)s"
        " AND tpep_pickup_datetime < %(end)s)",
    }
    counts = ", ".join(f"count(*) FILTER (WHERE {cond})" for cond in rules.values())
    any_flag = " OR ".join(f"({cond})" for cond in rules.values())
    cur.execute(
        f"SELECT {counts}, count(*) FILTER (WHERE {any_flag}) FROM stage",
        {"start": start, "end": end},
    )
    *by_rule, rows_flagged = cur.fetchone()

    cur.execute(f"DELETE FROM {tlc_yellow.RAW_TABLE} WHERE period = %s", (period,))
    rows_replaced = cur.rowcount
    cur.execute(
        f"INSERT INTO {tlc_yellow.RAW_TABLE} (source_file_id, period, {col_list}) "
        f"SELECT %s, %s, {col_list} FROM stage",
        (source_file_id, period),
    )
    rows_inserted = cur.rowcount
    pg.commit()
    return {
        "rows_read": rows_read,
        "rows_inserted": rows_inserted,
        "rows_flagged": rows_flagged,
        "details": {
            "flags": dict(zip(rules, by_rule, strict=True)),
            "rows_replaced": rows_replaced,
            "missing_optional_columns": report.missing_optional,
            "unexpected_columns": report.unexpected,
        },
    }
