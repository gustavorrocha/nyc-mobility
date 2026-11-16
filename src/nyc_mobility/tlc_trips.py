"""Adaptadores das viagens NYC TLC (Yellow, Green, FHV e HVFHV): origem, contrato e carga.

As quatro modalidades compartilham o formato (Parquet mensal, horário local de NY sem fuso) e o
fluxo de carga (staging temporário + substituição da competência em raw). Cada uma tem seu
contrato de campos; as colunas obrigatórias do contrato definem também a sinalização
`missing_required_value` (ex.: no FHV a TLC não exige zonas, então zona ausente não é problema).
"""

import io
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pyarrow.types as pt

from nyc_mobility.common import ContractError, period_bounds

BATCH_ROWS = 100_000

KIND_CHECKS = {
    "integer": pt.is_integer,
    "float": lambda t: pt.is_floating(t) or pt.is_integer(t),
    # Timestamps da TLC são horário local de NY sem fuso; um fuso explícito muda o contrato.
    "timestamp": lambda t: pt.is_timestamp(t) and t.tz is None,
    "text": lambda t: pt.is_string(t) or pt.is_large_string(t),
}


@dataclass(frozen=True)
class SchemaReport:
    columns: list[tuple[str, str]]  # (nome no arquivo, nome em raw), apenas presentes
    missing_optional: list[str]  # carregadas como NULL
    unexpected: list[str]  # presentes no arquivo, não previstas no contrato (não carregadas)


@dataclass(frozen=True, eq=False)  # identidade: cada modalidade é única
class TripAdapter:
    """Uma modalidade de viagens TLC. Na tabela raw, as colunas têm o nome da origem em minúsculas.

    `contract`: (coluna na origem, tipo, obrigatória). `pickup`/`dropoff`/`distance`: colunas
    (nome em raw) usadas nas sinalizações por registro.
    """

    DATASET: str
    contract: list[tuple[str, str, bool]]
    pickup: str
    dropoff: str
    distance: str | None
    SOURCE = "tlc"
    PERIODIC = True

    @property
    def raw_table(self) -> str:
        return f"raw.tlc_{self.DATASET}_trips"

    def url(self, period: str) -> str:
        return (
            "https://d37ci6vzurychx.cloudfront.net/trip-data/"
            f"{self.DATASET}_tripdata_{period}.parquet"
        )

    def validate(self, schema: pa.Schema) -> SchemaReport:
        # A TLC já alterou maiúsculas/minúsculas de colunas (ex.: airport_fee): casar sem distinção.
        by_lower = {name.lower(): name for name in schema.names}
        problems, columns, missing_optional = [], [], []
        for name, kind, required in self.contract:
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
            raise ContractError(f"Contrato tlc.{self.DATASET} violado: {'; '.join(problems)}")
        return SchemaReport(columns, missing_optional, sorted(by_lower.values()))

    def flag_rules(self) -> dict[str, str]:
        """Problemas por registro (SQL sobre a staging), contabilizados sem descartar nada."""
        required = [name.lower() for name, _, req in self.contract if req]
        rules = {
            "missing_required_value": " OR ".join(f"{c} IS NULL" for c in required),
            "dropoff_before_pickup": f"{self.dropoff} < {self.pickup}",
        }
        if self.distance:
            rules["negative_distance"] = f"{self.distance} < 0"
        rules["pickup_outside_period"] = (
            f"NOT ({self.pickup} >= %(start)s AND {self.pickup} < %(end)s)"
        )
        return rules

    def load(self, pg, path, period, source_file_id) -> dict:
        """Staging temporário + substituição da competência em raw (commit pelo chamador)."""
        parquet = pq.ParquetFile(path)
        report = self.validate(parquet.schema_arrow)
        src_cols = [src for src, _ in report.columns]
        raw_cols = [dst for _, dst in report.columns] + ["row_number"]
        col_list = ", ".join(raw_cols)
        cur = pg.cursor()
        cur.execute(
            f"CREATE TEMP TABLE stage ON COMMIT DROP AS "
            f"SELECT {col_list} FROM {self.raw_table} WITH NO DATA"
        )
        staged = 0
        with cur.copy(f"COPY stage ({col_list}) FROM STDIN (FORMAT csv)") as copy:
            for batch in parquet.iter_batches(batch_size=BATCH_ROWS, columns=src_cols):
                table = pa.Table.from_batches([batch]).select(src_cols)
                table = table.rename_columns(raw_cols[:-1])
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

        start, end = period_bounds(period)
        rules = self.flag_rules()
        counts = ", ".join(f"count(*) FILTER (WHERE {cond})" for cond in rules.values())
        any_flag = " OR ".join(f"({cond})" for cond in rules.values())
        cur.execute(
            f"SELECT {counts}, count(*) FILTER (WHERE {any_flag}) FROM stage",
            {"start": start, "end": end},
        )
        *by_rule, rows_flagged = cur.fetchone()

        cur.execute(f"DELETE FROM {self.raw_table} WHERE period = %s", (period,))
        rows_replaced = cur.rowcount
        cur.execute(
            f"INSERT INTO {self.raw_table} (source_file_id, period, {col_list}) "
            f"SELECT %s, %s, {col_list} FROM stage",
            (source_file_id, period),
        )
        return {
            "rows_read": rows_read,
            "rows_inserted": cur.rowcount,
            "rows_flagged": rows_flagged,
            "details": {
                "flags": dict(zip(rules, by_rule, strict=True)),
                "rows_replaced": rows_replaced,
                "missing_optional_columns": report.missing_optional,
                "unexpected_columns": report.unexpected,
            },
        }


_TAXI_FEES = [
    ("fare_amount", "float", False),
    ("extra", "float", False),
    ("mta_tax", "float", False),
    ("tip_amount", "float", False),
    ("tolls_amount", "float", False),
]

YELLOW = TripAdapter(
    "yellow",
    [
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
        *_TAXI_FEES,
        ("improvement_surcharge", "float", False),
        ("total_amount", "float", False),
        ("congestion_surcharge", "float", False),
        ("Airport_fee", "float", False),
        ("cbd_congestion_fee", "float", False),
    ],
    pickup="tpep_pickup_datetime",
    dropoff="tpep_dropoff_datetime",
    distance="trip_distance",
)

# Táxis verdes (boro taxis): mesmo modelo do Yellow, com prefixo lpep e campos próprios.
GREEN = TripAdapter(
    "green",
    [
        ("VendorID", "integer", False),
        ("lpep_pickup_datetime", "timestamp", True),
        ("lpep_dropoff_datetime", "timestamp", True),
        ("store_and_fwd_flag", "text", False),
        ("RatecodeID", "integer", False),
        ("PULocationID", "integer", True),
        ("DOLocationID", "integer", True),
        ("passenger_count", "integer", False),
        ("trip_distance", "float", True),
        *_TAXI_FEES,
        ("ehail_fee", "float", False),
        ("improvement_surcharge", "float", False),
        ("total_amount", "float", False),
        ("payment_type", "integer", False),
        ("trip_type", "integer", False),
        ("congestion_surcharge", "float", False),
        ("cbd_congestion_fee", "float", False),
    ],
    pickup="lpep_pickup_datetime",
    dropoff="lpep_dropoff_datetime",
    distance="trip_distance",
)

# FHV tradicional (bases, limusines): sem distância nem valores; zonas não são obrigatórias.
FHV = TripAdapter(
    "fhv",
    [
        ("dispatching_base_num", "text", False),
        ("pickup_datetime", "timestamp", True),
        ("dropOff_datetime", "timestamp", True),
        ("PUlocationID", "integer", False),
        ("DOlocationID", "integer", False),
        ("SR_Flag", "integer", False),
        ("Affiliated_base_number", "text", False),
    ],
    pickup="pickup_datetime",
    dropoff="dropoff_datetime",
    distance=None,
)

# HVFHV (aplicativos, ex.: Uber e Lyft): distância em trip_miles e valor em componentes.
FHVHV = TripAdapter(
    "fhvhv",
    [
        ("hvfhs_license_num", "text", False),
        ("dispatching_base_num", "text", False),
        ("originating_base_num", "text", False),
        ("request_datetime", "timestamp", False),
        ("on_scene_datetime", "timestamp", False),
        ("pickup_datetime", "timestamp", True),
        ("dropoff_datetime", "timestamp", True),
        ("PULocationID", "integer", True),
        ("DOLocationID", "integer", True),
        ("trip_miles", "float", True),
        ("trip_time", "integer", False),
        ("base_passenger_fare", "float", False),
        ("tolls", "float", False),
        ("bcf", "float", False),
        ("sales_tax", "float", False),
        ("congestion_surcharge", "float", False),
        ("airport_fee", "float", False),
        ("tips", "float", False),
        ("driver_pay", "float", False),
        ("shared_request_flag", "text", False),
        ("shared_match_flag", "text", False),
        ("access_a_ride_flag", "text", False),
        ("wav_request_flag", "text", False),
        ("wav_match_flag", "text", False),
        ("cbd_congestion_fee", "float", False),
    ],
    pickup="pickup_datetime",
    dropoff="dropoff_datetime",
    distance="trip_miles",
)

ADAPTERS = (YELLOW, GREEN, FHV, FHVHV)
