"""Adaptador do cadastro NYC TLC Taxi Zones: referência versionada, sem competência.

A versão publicada em raw é substituída inteira apenas quando o hash do arquivo muda; todas as
versões permanecem na landing e em metadata.source_files.
"""

import csv

from nyc_mobility.common import ContractError

SOURCE, DATASET, PERIODIC = "tlc", "taxi_zones", False
RAW_TABLE = "raw.tlc_taxi_zones"
HEADER = ["LocationID", "Borough", "Zone", "service_zone"]


def url(period=None) -> str:
    return "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"


def parse(path) -> list[tuple]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        header, *body = list(csv.reader(f))
    if [h.lower() for h in header] != [h.lower() for h in HEADER]:
        raise ContractError(f"Cabeçalho inesperado: {header} (esperado {HEADER})")
    if not body:
        raise ContractError("Cadastro de zonas vazio")
    rows, seen = [], set()
    for line, values in enumerate(body, start=2):
        if len(values) != len(HEADER):
            raise ContractError(f"Linha {line}: {len(values)} campos, esperado {len(HEADER)}")
        try:
            location_id = int(values[0])
        except ValueError:
            raise ContractError(f"Linha {line}: LocationID não inteiro: {values[0]!r}") from None
        if location_id in seen:
            raise ContractError(f"Linha {line}: LocationID duplicado: {location_id}")
        seen.add(location_id)
        rows.append((line - 1, location_id, *values[1:]))
    return rows


def load(pg, path, period, source_file_id) -> dict:
    """Valida o cadastro e substitui a versão publicada em raw (commit pelo chamador)."""
    rows = parse(path)
    cur = pg.cursor()
    cur.execute(f"DELETE FROM {RAW_TABLE}")
    rows_replaced = cur.rowcount
    columns = "source_file_id, row_number, locationid, borough, zone, service_zone"
    with cur.copy(f"COPY {RAW_TABLE} ({columns}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row((source_file_id, *row))
    # Valores vazios são preservados como na origem e apenas contabilizados.
    empty = sum(any(v == "" for v in row[2:]) for row in rows)
    return {
        "rows_read": len(rows),
        "rows_inserted": len(rows),
        "rows_flagged": empty,
        "details": {"flags": {"empty_value": empty}, "rows_replaced": rows_replaced},
    }
