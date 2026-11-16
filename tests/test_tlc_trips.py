import pyarrow as pa
import pytest

from nyc_mobility import landing
from nyc_mobility.common import ContractError, period_bounds
from nyc_mobility.tlc_trips import FHV, FHVHV, GREEN, YELLOW


def test_contract_accepts_2025_schema_and_reports_differences():
    schema = pa.schema(
        [
            ("tpep_pickup_datetime", pa.timestamp("us")),
            ("tpep_dropoff_datetime", pa.timestamp("us")),
            ("trip_distance", pa.float64()),
            ("PULocationID", pa.int32()),
            ("DOLocationID", pa.int32()),
            ("airport_fee", pa.float64()),  # caixa diferente da documentada
            ("new_fee", pa.float64()),
        ]
    )
    report = YELLOW.validate(schema)
    assert ("airport_fee", "airport_fee") in report.columns
    assert "VendorID" in report.missing_optional
    assert report.unexpected == ["new_fee"]


@pytest.mark.parametrize(
    ("field", "message"),
    [
        (("PULocationID", pa.string()), "PULocationID: tipo string"),
        (("tpep_pickup_datetime", pa.timestamp("us", tz="UTC")), "tpep_pickup_datetime: tipo"),
    ],
)
def test_contract_rejects_incompatible_types(field, message):
    base = {
        "tpep_pickup_datetime": pa.timestamp("us"),
        "tpep_dropoff_datetime": pa.timestamp("us"),
        "trip_distance": pa.float64(),
        "PULocationID": pa.int32(),
        "DOLocationID": pa.int32(),
    }
    base[field[0]] = field[1]
    with pytest.raises(ContractError, match=message):
        YELLOW.validate(pa.schema(list(base.items())))


def test_contract_rejects_missing_required_column():
    with pytest.raises(ContractError, match="obrigatória ausente: trip_distance"):
        YELLOW.validate(pa.schema([("tpep_pickup_datetime", pa.timestamp("us"))]))


def test_period_bounds():
    assert [d.isoformat() for d in period_bounds("2025-12")] == [
        "2025-12-01T00:00:00",
        "2026-01-01T00:00:00",
    ]
    with pytest.raises(ValueError):
        period_bounds("2025-13")


def test_landing_versions_by_hash_without_overwriting(tmp_path):
    src = tmp_path / "src" / "file.parquet"
    src.parent.mkdir()
    src.write_bytes(b"v1")
    dest = tmp_path / "landing"
    first = landing.download(src.as_uri(), dest)
    again = landing.download(src.as_uri(), dest)
    src.write_bytes(b"v2-republicado")
    second = landing.download(src.as_uri(), dest)

    assert first.path == again.path and first.path.read_bytes() == b"v1"
    assert second.path != first.path and second.path.read_bytes() == b"v2-republicado"
    assert first.sha256 == "3bfc269594ef649228e9a74bab00f042efc91d5acc6fbee31a382e80d42388fe"
    assert not list(dest.glob("*.part"))


TS = pa.timestamp("us")
# Esquemas publicados pela TLC em 2025-01 (lidos do rodapé dos Parquets oficiais).
REAL_2025_SCHEMAS = {
    GREEN: [
        ("VendorID", pa.int32()),
        ("lpep_pickup_datetime", TS),
        ("lpep_dropoff_datetime", TS),
        ("store_and_fwd_flag", pa.large_string()),
        ("RatecodeID", pa.int64()),
        ("PULocationID", pa.int32()),
        ("DOLocationID", pa.int32()),
        ("passenger_count", pa.int64()),
        ("trip_distance", pa.float64()),
        ("fare_amount", pa.float64()),
        ("extra", pa.float64()),
        ("mta_tax", pa.float64()),
        ("tip_amount", pa.float64()),
        ("tolls_amount", pa.float64()),
        ("ehail_fee", pa.float64()),
        ("improvement_surcharge", pa.float64()),
        ("total_amount", pa.float64()),
        ("payment_type", pa.int64()),
        ("trip_type", pa.int64()),
        ("congestion_surcharge", pa.float64()),
        ("cbd_congestion_fee", pa.float64()),
    ],
    FHV: [
        ("dispatching_base_num", pa.large_string()),
        ("pickup_datetime", TS),
        ("dropOff_datetime", TS),
        ("PUlocationID", pa.int64()),
        ("DOlocationID", pa.int64()),
        ("SR_Flag", pa.int64()),
        ("Affiliated_base_number", pa.large_string()),
    ],
    FHVHV: [
        ("hvfhs_license_num", pa.large_string()),
        ("dispatching_base_num", pa.large_string()),
        ("originating_base_num", pa.large_string()),
        ("request_datetime", TS),
        ("on_scene_datetime", TS),
        ("pickup_datetime", TS),
        ("dropoff_datetime", TS),
        ("PULocationID", pa.int32()),
        ("DOLocationID", pa.int32()),
        ("trip_miles", pa.float64()),
        ("trip_time", pa.int64()),
        ("base_passenger_fare", pa.float64()),
        ("tolls", pa.float64()),
        ("bcf", pa.float64()),
        ("sales_tax", pa.float64()),
        ("congestion_surcharge", pa.float64()),
        ("airport_fee", pa.float64()),
        ("tips", pa.float64()),
        ("driver_pay", pa.float64()),
        ("shared_request_flag", pa.large_string()),
        ("shared_match_flag", pa.large_string()),
        ("access_a_ride_flag", pa.large_string()),
        ("wav_request_flag", pa.large_string()),
        ("wav_match_flag", pa.large_string()),
        ("cbd_congestion_fee", pa.float64()),
    ],
}


@pytest.mark.parametrize("adapter", list(REAL_2025_SCHEMAS), ids=lambda a: a.DATASET)
def test_contracts_match_published_2025_schemas(adapter):
    report = adapter.validate(pa.schema(REAL_2025_SCHEMAS[adapter]))
    assert report.missing_optional == [] and report.unexpected == []
    assert adapter.raw_table == f"raw.tlc_{adapter.DATASET}_trips"
    assert adapter.url("2025-01").endswith(f"/trip-data/{adapter.DATASET}_tripdata_2025-01.parquet")


def test_fhv_does_not_require_zones_or_distance():
    report = FHV.validate(pa.schema([("pickup_datetime", TS), ("dropOff_datetime", TS)]))
    assert "PUlocationID" in report.missing_optional
    rules = FHV.flag_rules()
    assert rules["missing_required_value"] == "pickup_datetime IS NULL OR dropoff_datetime IS NULL"
    assert "negative_distance" not in rules


def test_yellow_flag_rules_are_unchanged():
    assert YELLOW.flag_rules() == {
        "missing_required_value": "tpep_pickup_datetime IS NULL OR tpep_dropoff_datetime IS NULL"
        " OR trip_distance IS NULL OR pulocationid IS NULL OR dolocationid IS NULL",
        "dropoff_before_pickup": "tpep_dropoff_datetime < tpep_pickup_datetime",
        "negative_distance": "trip_distance < 0",
        "pickup_outside_period": "NOT (tpep_pickup_datetime >= %(start)s"
        " AND tpep_pickup_datetime < %(end)s)",
    }
    assert FHVHV.flag_rules()["negative_distance"] == "trip_miles < 0"
