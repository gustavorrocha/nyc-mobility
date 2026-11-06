"""Open-Meteo e Taxi Zones: tratamento temporal, contrato, versionamento e idempotência."""

import json

import pytest
import sqlalchemy as sa

from nyc_mobility import openmeteo
from nyc_mobility.common import ContractError
from nyc_mobility.ingest import ingest


def weather_json(path, period, generation_ms=0.1, drop_last_hour=False, null_rain_at=None):
    times = openmeteo.expected_hours(period)[: -1 if drop_last_hour else None]
    hourly = {"time": times, **{v: [1] * len(times) for v in openmeteo.VARIABLES}}
    if null_rain_at is not None:
        hourly["rain"][null_rain_at] = None
    payload = {
        "latitude": 40.75,
        "longitude": -74.0,
        "generationtime_ms": generation_ms,
        "elevation": 33.0,
        "hourly_units": {"time": "unixtime"},
        "hourly": hourly,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path.as_uri()


def zones_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['"LocationID","Borough","Zone","service_zone"', *rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.as_uri()


def rows(engine, sql):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql)).all()


@pytest.mark.parametrize(
    ("period", "hours"), [("2025-01", 744), ("2025-03", 743), ("2025-11", 721)]
)
def test_expected_hours_follow_local_month_and_dst(period, hours):
    expected = openmeteo.expected_hours(period)
    assert len(expected) == hours
    assert all(b - a == 3600 for a, b in zip(expected, expected[1:], strict=False))


def test_january_starts_at_local_midnight_in_utc():
    assert openmeteo.expected_hours("2025-01")[0] == 1735707600  # 2025-01-01T05:00:00Z


def test_url_requests_era5_unixtime_for_the_local_month():
    url = openmeteo.url("2025-02")
    for part in (
        "start_date=2025-02-01",
        "end_date=2025-02-28",
        "models=era5",
        "timeformat=unixtime",
        "timezone=America%2FNew_York",
    ):
        assert part in url


def test_fingerprint_ignores_generation_time(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    weather_json(a, "2025-01", generation_ms=0.1)
    weather_json(b, "2025-01", generation_ms=9.9)
    assert openmeteo.fingerprint(a) == openmeteo.fingerprint(b)
    assert a.read_bytes() != b.read_bytes()


def test_incomplete_coverage_is_a_contract_error(tmp_path):
    path = tmp_path / "w.json"
    weather_json(path, "2025-01", drop_last_hour=True)
    with pytest.raises(ContractError, match="743 horas recebidas, 744 esperadas"):
        openmeteo.validate(json.loads(path.read_text()), "2025-01")


def test_weather_load_skip_and_failure_preserve_version(engine, landing, tmp_path):
    first = ingest(
        "openmeteo.era5",
        "2025-01",
        url=weather_json(tmp_path / "w1.json", "2025-01", null_rain_at=3),
    )
    assert (first["status"], first["rows_inserted"], first["rows_flagged"]) == ("succeeded", 744, 1)
    assert first["details"]["flags"] == {"null_rain": 1}

    # Mesma série com outro generationtime_ms: mesma versão, nenhuma recarga.
    again = ingest(
        "openmeteo.era5",
        "2025-01",
        url=weather_json(tmp_path / "w2.json", "2025-01", generation_ms=7.7, null_rain_at=3),
    )
    assert again["status"] == "skipped"
    assert again["source_file_id"] == first["source_file_id"]
    assert len(list((landing / "openmeteo/era5/2025-01").iterdir())) == 1

    with pytest.raises(ContractError):
        ingest(
            "openmeteo.era5",
            "2025-01",
            url=weather_json(tmp_path / "w3.json", "2025-01", drop_last_hour=True),
        )

    published = rows(
        engine,
        "SELECT source_file_id, count(*), min(time_unix), "
        "count(*) FILTER (WHERE rain IS NULL) FROM raw.openmeteo_weather_hourly "
        "WHERE period = '2025-01' GROUP BY 1",
    )
    assert published == [(first["source_file_id"], 744, 1735707600, 1)]


def test_zones_are_a_versioned_reference(engine, landing, tmp_path):
    src = tmp_path / "zones.csv"
    v1 = ingest(
        "tlc.taxi_zones",
        url=zones_csv(src, ['1,"EWR","Newark Airport","EWR"', '264,"Unknown","N/A",""']),
    )
    assert (v1["status"], v1["rows_inserted"], v1["rows_flagged"]) == ("succeeded", 2, 1)
    assert ingest("tlc.taxi_zones", url=src.as_uri())["status"] == "skipped"

    v2 = ingest(
        "tlc.taxi_zones",
        url=zones_csv(
            src,
            [
                '1,"EWR","Newark Airport","EWR"',
                '2,"Queens","Jamaica Bay","Boro Zone"',
                '264,"Unknown","N/A",""',
            ],
        ),
    )
    assert v2["source_file_id"] != v1["source_file_id"]
    assert v2["details"]["rows_replaced"] == 2

    with pytest.raises(ContractError, match="duplicado"):
        ingest("tlc.taxi_zones", url=zones_csv(src, ['1,"A","B","C"', '1,"A","B","C"']))

    published = rows(engine, "SELECT source_file_id, count(*) FROM raw.tlc_taxi_zones GROUP BY 1")
    assert published == [(v2["source_file_id"], 3)]
    statuses = rows(
        engine,
        "SELECT status, period FROM metadata.ingestion_runs r "
        "JOIN metadata.sources s USING (source_id) WHERE s.dataset = 'taxi_zones' "
        "ORDER BY run_id",
    )
    assert statuses == [
        ("succeeded", None),
        ("skipped", None),
        ("succeeded", None),
        ("failed", None),
    ]
    # String vazia preservada como na origem (não vira NULL em raw).
    assert rows(engine, "SELECT service_zone FROM raw.tlc_taxi_zones WHERE locationid = 264") == [
        ("",)
    ]


def test_periodicity_is_enforced():
    with pytest.raises(ValueError, match="sem competência"):
        ingest("tlc.taxi_zones", "2025-01")
    with pytest.raises(ValueError, match="Competência inválida"):
        ingest("openmeteo.era5", None)
