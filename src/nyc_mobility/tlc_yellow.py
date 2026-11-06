"""Adaptador NYC TLC Yellow Taxi: origem, contrato de campos e validação estrutural."""

from dataclasses import dataclass

import pyarrow as pa
import pyarrow.types as pt

SOURCE, DATASET = "tlc", "yellow"
RAW_TABLE = "raw.tlc_yellow_trips"

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


class ContractError(ValueError):
    """Falha estrutural: o arquivo não atende ao contrato e não pode ser carregado."""


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
