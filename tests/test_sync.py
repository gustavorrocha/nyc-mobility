from datetime import date
from io import BytesIO
from urllib.error import HTTPError

import pytest

from nyc_mobility import sync

REAL_CATALOGUE = sync.catalogue


@pytest.fixture(autouse=True)
def known_catalogue(monkeypatch):
    monkeypatch.setattr(
        sync,
        "catalogue",
        lambda: {
            ("tlc." + dataset, period)
            for dataset in ("yellow", "green", "fhv", "fhvhv")
            for period in ("2025-01", "2025-02")
        },
    )


def test_catalogue_reads_links_not_commented_placeholders(monkeypatch):
    parser = sync._Catalogue()
    parser.feed(
        '<a href="https://example.test/yellow_tripdata_2025-01.parquet">data</a>'
        '<!-- <a href="https://example.test/green_tripdata_2025-02.parquet">future</a> -->'
    )
    assert parser.files == {("tlc.yellow", "2025-01")}


def test_changed_catalogue_format_is_an_error_not_empty_availability(monkeypatch):
    monkeypatch.setattr(sync, "urlopen", lambda *a, **kw: BytesIO(b"<html>no data links</html>"))
    with pytest.raises(ValueError, match="Catálogo TLC"):
        REAL_CATALOGUE()


def test_unlisted_month_is_pending_without_probing_an_unpublished_object(monkeypatch):
    monkeypatch.setattr(sync, "loaded", lambda: {})
    monkeypatch.setattr(sync, "catalogue", lambda: {("tlc.yellow", "2025-01")})
    monkeypatch.setattr(sync, "changed", lambda *args: True)
    assert sync.plan("2025-01", "2025-01")["waiting"] == [
        {"period": "2025-01", "datasets": ["tlc.green", "tlc.fhv"]}
    ]


def test_period_range_validates_and_excludes_current_month():
    assert sync.periods("2025-12", "2026-02", date(2026, 3, 1)) == ["2025-12", "2026-01", "2026-02"]
    with pytest.raises(ValueError):
        sync.periods("2026-03", "2026-03", date(2026, 3, 1))
    with pytest.raises(ValueError):
        sync.periods("2025-03", "2025-02", date(2026, 3, 1))


def test_probe_distinguishes_not_published_from_operational_failures(monkeypatch):
    def missing(*args, **kwargs):
        raise HTTPError("https://example.test", 404, "missing", {}, None)

    monkeypatch.setattr(sync, "urlopen", missing)
    assert sync.changed("https://example.test", None) is None
    for code in (403, 429, 500):

        def failed(*args, code=code, **kwargs):
            raise HTTPError("https://example.test", code, "failure", {}, None)

        monkeypatch.setattr(sync, "urlopen", failed)
        with pytest.raises(HTTPError):
            sync.changed("https://example.test", None)


def test_probe_reuses_validators_without_downloading(monkeypatch):
    def unchanged(request, **kwargs):
        assert request.method == "HEAD"
        assert request.get_header("If-none-match") == '"v1"'
        raise HTTPError(request.full_url, 304, "unchanged", {}, None)

    monkeypatch.setattr(sync, "urlopen", unchanged)
    assert sync.changed("https://example.test", {"ETag": '"v1"'}) is False


def test_plan_waits_for_all_requested_sources_and_keeps_hvfhv_optional(monkeypatch):
    monkeypatch.setattr(sync, "loaded", lambda: {})
    monkeypatch.setattr(sync, "changed", lambda url, previous: None if "green" in url else True)
    plan = sync.plan("2025-01", "2025-02")
    assert plan["ready"] == []
    assert plan["waiting"] == [
        {"period": "2025-01", "datasets": ["tlc.green"]},
        {"period": "2025-02", "datasets": ["tlc.green"]},
    ]
    monkeypatch.setattr(sync, "changed", lambda *args: True)
    assert "tlc.fhvhv" not in sync.plan("2025-01", "2025-01")["ready"][0]["datasets"]
    assert (
        "tlc.fhvhv" in sync.plan("2025-01", "2025-01", include_hvfhv=True)["ready"][0]["datasets"]
    )


def test_sync_is_bounded_and_only_builds_after_success(monkeypatch):
    monkeypatch.setattr(
        sync,
        "plan",
        lambda *args, **kw: {
            "ready": [
                {"period": "2025-01", "datasets": ["tlc.yellow"]},
                {"period": "2025-02", "datasets": ["tlc.yellow"]},
            ],
            "waiting": [],
        },
    )
    processed = []

    def ingest(dataset, period=None):
        processed.append((dataset, period))
        return {"status": "succeeded"}

    monkeypatch.setattr(sync, "ingest", ingest)
    monkeypatch.setattr(sync, "needs_build", lambda: True)
    monkeypatch.setattr(sync, "build", lambda: {"status": "succeeded", "returncode": 0})
    result = sync.synchronize("2025-01", "2025-02", max_periods=1)
    assert ("tlc.yellow", "2025-01") in processed
    assert ("tlc.yellow", "2025-02") not in processed
    assert result["build"]["status"] == "succeeded"
    processed.clear()
    assert sync.synchronize("2025-01", "2025-02", dry_run=True)["build"] is None
    assert processed == []


def test_partial_ingestion_failure_never_builds(monkeypatch):
    monkeypatch.setattr(
        sync,
        "plan",
        lambda *args, **kw: {
            "ready": [{"period": "2025-01", "datasets": ["tlc.yellow"]}],
            "waiting": [],
        },
    )

    def failed(dataset, period=None):
        if period:
            raise OSError("network failure")
        return {"status": "skipped"}

    monkeypatch.setattr(sync, "ingest", failed)
    monkeypatch.setattr(sync, "build", lambda: pytest.fail("Must not build incomplete ingestion"))
    with pytest.raises(OSError):
        sync.synchronize("2025-01", "2025-01")
