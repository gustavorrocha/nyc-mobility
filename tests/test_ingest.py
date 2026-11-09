"""Integração da ingestão Yellow com PostgreSQL (banco nyc_mobility_test), sem rede."""

import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
import sqlalchemy as sa

from nyc_mobility import tlc_yellow
from nyc_mobility.ingest import ConcurrentLoadError, ingest


def scalar(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).scalar()


def raw_files(engine, period):
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT source_file_id, count(*) FROM raw.tlc_yellow_trips "
                "WHERE period = :p GROUP BY 1"
            ),
            {"p": period},
        ).all()


def statuses(engine, period):
    with engine.connect() as conn:
        return list(
            conn.scalars(
                sa.text("SELECT status FROM metadata.ingestion_runs WHERE period = :p ORDER BY 1"),
                {"p": period},
            )
        )


def test_load_skip_and_forced_reload_do_not_duplicate(engine, landing, yellow_parquet, tmp_path):
    url = yellow_parquet(tmp_path / "src" / "y.parquet", "2025-01")

    first = ingest("tlc.yellow", "2025-01", url=url)
    assert first["status"] == "succeeded"
    assert (first["rows_read"], first["rows_inserted"], first["rows_flagged"]) == (5, 5, 1)
    assert first["details"]["flags"]["dropoff_before_pickup"] == 1
    assert "RatecodeID" in first["details"]["missing_optional_columns"]

    assert ingest("tlc.yellow", "2025-01", url=url)["status"] == "skipped"
    assert ingest("tlc.yellow", "2025-01", url=url, force=True)["status"] == "succeeded"

    assert raw_files(engine, "2025-01") == [(first["source_file_id"], 5)]
    assert sorted(statuses(engine, "2025-01")) == ["skipped", "succeeded", "succeeded"]
    path = scalar(engine, "SELECT landing_path FROM metadata.source_files")
    assert (landing / path).is_file() and path.startswith("tlc/yellow/2025-01/")
    # Valor original preservado e NULL mantido (sem substituição por zero).
    assert (
        scalar(
            engine,
            "SELECT count(*) FROM raw.tlc_yellow_trips WHERE passenger_count IS NULL "
            "AND period = '2025-01'",
        )
        == 1
    )


def test_republished_file_replaces_only_its_period(engine, landing, yellow_parquet, tmp_path):
    src = tmp_path / "src" / "y.parquet"
    other = ingest("tlc.yellow", "2025-03", url=yellow_parquet(tmp_path / "o.parquet", "2025-03"))
    ingest("tlc.yellow", "2025-02", url=yellow_parquet(src, "2025-02", rows=5))

    republished = ingest("tlc.yellow", "2025-02", url=yellow_parquet(src, "2025-02", rows=3))

    assert republished["details"]["rows_replaced"] == 5
    assert raw_files(engine, "2025-02") == [(republished["source_file_id"], 3)]
    assert raw_files(engine, "2025-03") == [(other["source_file_id"], 5)]
    assert len(list((landing / "tlc/yellow/2025-02").iterdir())) == 2  # versões preservadas


def test_failed_load_preserves_published_version(engine, landing, yellow_parquet, tmp_path):
    src = tmp_path / "src" / "y.parquet"
    published = ingest("tlc.yellow", "2025-04", url=yellow_parquet(src, "2025-04"))

    # PULocationID fora do intervalo de integer: a COPY falha no meio da transação.
    with pytest.raises(Exception, match="out of range"):
        ingest(
            "tlc.yellow", "2025-04", url=yellow_parquet(src, "2025-04", rows=7, pu_location=2**40)
        )
    bad = yellow_parquet(src, "2025-04", drop_columns=["trip_distance"])
    with pytest.raises(tlc_yellow.ContractError):
        ingest("tlc.yellow", "2025-04", url=bad)

    assert raw_files(engine, "2025-04") == [(published["source_file_id"], 5)]
    assert statuses(engine, "2025-04") == ["failed", "failed", "succeeded"]
    error = scalar(
        engine,
        "SELECT error FROM metadata.ingestion_runs WHERE period = '2025-04' "
        "AND status = 'failed' ORDER BY run_id DESC LIMIT 1",
    )
    assert error.startswith("ContractError")


def test_concurrent_load_is_rejected(engine, landing, yellow_parquet, tmp_path):
    url = yellow_parquet(tmp_path / "src" / "y.parquet", "2025-05")
    ingest("tlc.yellow", "2025-05", url=url)
    source_id = scalar(engine, "SELECT source_id FROM metadata.sources WHERE dataset = 'yellow'")
    with engine.connect() as holder:
        holder.execute(sa.text("SELECT pg_advisory_lock(:s, 202505)"), {"s": source_id})
        with pytest.raises(ConcurrentLoadError):
            ingest("tlc.yellow", "2025-05", url=url, force=True)
    assert raw_files(engine, "2025-05")[0][1] == 5


@pytest.fixture
def http_root(tmp_path):
    """Servidor HTTP local; responde 304 a If-Modified-Since quando o arquivo não mudou."""

    class Quiet(SimpleHTTPRequestHandler):
        def log_request(self, code="-", size="-"):
            requests.append(f"{self.path} {int(code)}")

        def log_message(self, *args):
            pass

    requests = []
    root = tmp_path / "www"
    root.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=root))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_port}", requests
    server.shutdown()


def test_unchanged_source_is_not_downloaded_again(engine, landing, yellow_parquet, http_root):
    root, base, requests = http_root
    yellow_parquet(root / "y.parquet", "2023-05")
    url = f"{base}/y.parquet"

    first = ingest("tlc.yellow", "2023-05", url=url)
    assert first["status"] == "succeeded"

    # Origem inalterada: requisição condicional, 304, nenhum download e nenhuma versão nova.
    again = ingest("tlc.yellow", "2023-05", url=url)
    assert (again["status"], again["source_file_id"]) == ("skipped", first["source_file_id"])
    assert "304" in again["reason"]
    assert requests == ["/y.parquet 200", "/y.parquet 304"]

    # --force ignora a validação condicional.
    assert ingest("tlc.yellow", "2023-05", url=url, force=True)["status"] == "succeeded"
    assert requests[-1] == "/y.parquet 200"

    # Arquivo republicado (conteúdo e data novos): baixado e carregado como nova versão.
    yellow_parquet(root / "y.parquet", "2023-05", rows=3)
    os.utime(root / "y.parquet", (time.time() + 60, time.time() + 60))
    republished = ingest("tlc.yellow", "2023-05", url=url)
    assert republished["status"] == "succeeded"
    assert republished["source_file_id"] != first["source_file_id"]
    assert raw_files(engine, "2023-05") == [(republished["source_file_id"], 3)]
