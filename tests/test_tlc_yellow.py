import pyarrow as pa
import pytest

from nyc_mobility import landing, tlc_yellow
from nyc_mobility.ingest import period_bounds


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
    report = tlc_yellow.validate(schema)
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
    with pytest.raises(tlc_yellow.ContractError, match=message):
        tlc_yellow.validate(pa.schema(list(base.items())))


def test_contract_rejects_missing_required_column():
    with pytest.raises(tlc_yellow.ContractError, match="obrigatória ausente: trip_distance"):
        tlc_yellow.validate(pa.schema([("tpep_pickup_datetime", pa.timestamp("us"))]))


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
