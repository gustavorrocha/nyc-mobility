"""Adaptador NYC TLC Yellow Taxi: origem, contrato de campos e validação estrutural."""

import io
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pyarrow.types as pt

from nyc_mobility.common import ContractError, period_bounds

SOURCE, DATASET, PERIODIC = "tlc", "yellow", True
RAW_TABLE = "raw.tlc_yellow_trips"
BATCH_ROWS = 100_000

KIND_CHECKS = {
    "integer": pt.is_integer,
    "float": lambda t: pt.is_floating(t) or pt.is_integer(t),
    # Timestamps da TLC são horário local de NY sem fuso; um fuso explícito muda o contrato.
    "timestamp": lambda t: pt.is_timestamp(t) and t.tz is None,
    "text": lambda t: pt.is_string(t) or pt.is_large_string(t),
}

# (coluna na origem, tipo, obrigatória). Na tabela raw o nome é o mesmo, em minúsculas.
CONTRACT = [
    ("VendorID", "integer", False),
    ("tpep_pickup_datetime", "timestamp", True),
    ("tpep_dropoff_datetime", "timestamp", True),
    ("passenger_count", "integer", False),
    ("trip_distance", "float", True),
    ("RatecodeID", "integer", False),
    ("store_and_fwd_flag", "text", False),
    ("PULocationID", "integer", True),
    ("DOLocationID", "integer", True),
    ("payment_type", "integer", False),
    ("fare_amount", "float", False),
    ("extra", "float", False),
    ("mta_tax", "float", False),
    ("tip_amount", "float", False),
    ("tolls_amount", "float", False),
    ("improvement_surcharge", "float", False),
    ("total_amount", "float", False),
    ("congestion_surcharge", "float", False),
    ("Airport_fee", "float", False),
    ("cbd_congestion_fee", "float", False),
]


def url(period: str) -> str:
    return f"https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{period}.parquet"


@dataclass(frozen=True)
class SchemaReport:
    columns: list[tuple[str, str]]  # (nome no arquivo, nome em raw), apenas presentes
    missing_optional: list[str]  # carregadas como NULL
    unexpected: list[str]  # presentes no arquivo, não previstas no contrato (não carregadas)


def validate(schema: pa.Schema) -> SchemaReport:
    # A TLC já alterou maiúsculas/minúsculas de colunas (ex.: airport_fee): casar sem distinção.
    by_lower = {name.lower(): name for name in schema.names}
    problems, columns, missing_optional = [], [], []
    for name, kind, required in CONTRACT:
        found = by_lower.pop(name.lower(), None)
        if found is None and required:
            problems.append(f"coluna obrigatória ausente: {name}")
        elif found is None:
            missing_optional.append(name)
        elif not KIND_CHECKS[kind](schema.field(found).type):
            problems.append(f"{found}: tipo {schema.field(found).type}, esperado {kind}")
        else:
            columns.append((found, name.lower()))
    if problems:
        raise ContractError(f"Contrato tlc.yellow violado: {'; '.join(problems)}")
    return SchemaReport(columns, missing_optional, sorted(by_lower.values()))


def load(pg, path, period, source_file_id) -> dict:
    """Staging temporário + substituição da competência em raw (commit pelo chamador)."""
    parquet = pq.ParquetFile(path)
    report = validate(parquet.schema_arrow)
    src_cols = [src for src, _ in report.columns]
    raw_cols = [dst for _, dst in report.columns] + ["row_number"]
    col_list = ", ".join(raw_cols)
    cur = pg.cursor()
    cur.execute(
        f"CREATE TEMP TABLE stage ON COMMIT DROP AS SELECT {col_list} FROM {RAW_TABLE} WITH NO DATA"
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

    cur.execute(f"DELETE FROM {RAW_TABLE} WHERE period = %s", (period,))
    rows_replaced = cur.rowcount
    cur.execute(
        f"INSERT INTO {RAW_TABLE} (source_file_id, period, {col_list}) "
        f"SELECT %s, %s, {col_list} FROM stage",
        (source_file_id, period),
    )
    rows_inserted = cur.rowcount
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
