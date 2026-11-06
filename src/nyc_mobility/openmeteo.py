"""Adaptador Open-Meteo (reanálise ERA5): clima horário histórico de Manhattan.

Referência temporal: `time_unix` é o instante UTC (Unix) do fim da hora. Para variáveis
acumuladas (precipitation, rain, snowfall) o valor é a soma da hora anterior, ou seja, o
intervalo (time_unix - 1h, time_unix]; as demais (temperatura, umidade, vento, weather_code)
são instantâneas. A competência é o mês local de NY, alinhada às corridas da TLC.
"""

import hashlib
import json
import urllib.parse
from datetime import timedelta
from zoneinfo import ZoneInfo

from nyc_mobility.common import ContractError, period_bounds

SOURCE, DATASET, PERIODIC = "openmeteo", "era5", True
RAW_TABLE = "raw.openmeteo_weather_hourly"
TIMEZONE = "America/New_York"
LATITUDE, LONGITUDE = 40.7812, -73.9665  # Central Park; a API ajusta para a grade ERA5
VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "snowfall",
    "weather_code",
    "wind_speed_10m",
]
VOLATILE_FIELDS = ("generationtime_ms",)  # muda a cada chamada; não faz parte do conteúdo


def url(period: str) -> str:
    start, end = period_bounds(period)
    query = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": start.date().isoformat(),
        "end_date": (end - timedelta(days=1)).date().isoformat(),
        "hourly": ",".join(VARIABLES),
        "models": "era5",
        "timezone": TIMEZONE,  # define os limites dos dias locais
        "timeformat": "unixtime",  # timestamps inequívocos em UTC
    }
    return f"https://archive-api.open-meteo.com/v1/archive?{urllib.parse.urlencode(query)}"


def landing_name(period: str) -> str:
    return f"era5_hourly_{period}.json"


def fingerprint(path) -> str:
    """SHA-256 do conteúdo canônico (sem campos voláteis, chaves ordenadas)."""
    data = json.loads(path.read_bytes())
    for field in VOLATILE_FIELDS:
        data.pop(field, None)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def expected_hours(period: str) -> list[int]:
    """Instantes Unix de todas as horas do mês local de NY (743/745 em meses com DST)."""
    start, end = (d.replace(tzinfo=ZoneInfo(TIMEZONE)) for d in period_bounds(period))
    return list(range(int(start.timestamp()), int(end.timestamp()), 3600))


def validate(data: dict, period: str) -> dict:
    hourly = data.get("hourly")
    if not isinstance(hourly, dict) or "time" not in hourly:
        raise ContractError("Resposta Open-Meteo sem bloco hourly.time")
    missing = [v for v in VARIABLES if v not in hourly]
    if missing:
        raise ContractError(f"Variáveis ausentes: {', '.join(missing)}")
    times = hourly["time"]
    bad_lengths = [v for v in VARIABLES if len(hourly[v]) != len(times)]
    if bad_lengths:
        raise ContractError(f"Séries com tamanho diferente de time: {', '.join(bad_lengths)}")
    if not all(isinstance(t, int) for t in times):
        raise ContractError("hourly.time deve conter timestamps Unix inteiros")
    expected = expected_hours(period)
    if times != expected:
        missing_hours = len(set(expected) - set(times))
        extra_hours = len(set(times) - set(expected))
        raise ContractError(
            f"Cobertura incompatível com {period}: {len(times)} horas recebidas, "
            f"{len(expected)} esperadas ({missing_hours} ausentes, {extra_hours} fora do período "
            "ou fora de ordem/duplicadas)"
        )
    return hourly


def load(pg, path, period, source_file_id) -> dict:
    """Valida a resposta e substitui a competência em raw (commit pelo chamador)."""
    data = json.loads(path.read_bytes())
    hourly = validate(data, period)
    cur = pg.cursor()
    cur.execute(f"DELETE FROM {RAW_TABLE} WHERE period = %s", (period,))
    rows_replaced = cur.rowcount
    columns = ", ".join(
        ["source_file_id", "period", "time_unix", "latitude", "longitude", *VARIABLES]
    )
    rows = list(zip(hourly["time"], *(hourly[v] for v in VARIABLES), strict=True))
    with cur.copy(f"COPY {RAW_TABLE} ({columns}) FROM STDIN") as copy:
        for time_unix, *values in rows:
            copy.write_row(
                (
                    source_file_id,
                    period,
                    time_unix,
                    data.get("latitude"),
                    data.get("longitude"),
                    *values,
                )
            )
    # Valores ausentes ficam NULL em raw e são contabilizados por variável.
    nulls = {v: sum(x is None for x in hourly[v]) for v in VARIABLES}
    return {
        "rows_read": len(rows),
        "rows_inserted": len(rows),
        "rows_flagged": sum(any(x is None for x in values) for _, *values in rows),
        "details": {
            "flags": {f"null_{v}": n for v, n in nulls.items() if n},
            "rows_replaced": rows_replaced,
            "units": data.get("hourly_units", {}),
            "grid": {k: data.get(k) for k in ("latitude", "longitude", "elevation")},
        },
    }
