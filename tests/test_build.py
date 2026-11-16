import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from nyc_mobility import build as runner


@pytest.mark.parametrize(
    ("returncode", "node_status", "expected"),
    [(0, "pass", "succeeded"), (1, "fail", "failed"), (1, "error", "failed")],
)
def test_build_records_outcome_and_each_node(
    engine, monkeypatch, returncode, node_status, expected
):
    monkeypatch.setenv("POSTGRES_DB", engine.url.database)

    def dbt(cmd, **kwargs):
        from pathlib import Path

        target = Path(cmd[cmd.index("--target-path") + 1])
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT status FROM metadata.analytics_runs ORDER BY run_id DESC LIMIT 1"
                    )
                )
                == "running"
            )
        target.mkdir(exist_ok=True)
        target.joinpath("run_results.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "unique_id": "test.nyc_mobility.example",
                            "status": node_status,
                            "message": "fixture result",
                            "execution_time": 0.1,
                            "failures": 1,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(runner.subprocess, "run", dbt)
    result = runner.build(period="2025-01")
    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.text("SELECT * FROM metadata.analytics_runs WHERE run_id = :id"),
                {"id": result["run_id"]},
            )
            .mappings()
            .one()
        )
    assert row["status"] == expected
    assert row["period"] == "2025-01"
    assert row["finished_at"] >= row["started_at"]
    assert row["node_results"][0]["status"] == node_status
    assert row["node_results"][0]["message"] == "fixture result"
    assert result["returncode"] == returncode


def test_missing_artifact_and_startup_error_are_recorded(engine, monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", engine.url.database)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    first = runner.build()
    assert first["status"] == "failed" and first["returncode"] != 0

    def missing(*args, **kwargs):
        raise FileNotFoundError("dbt não encontrado")

    monkeypatch.setattr(runner.subprocess, "run", missing)
    second = runner.build()
    with engine.connect() as conn:
        error = conn.scalar(
            sa.text("SELECT error FROM metadata.analytics_runs WHERE run_id = :id"),
            {"id": second["run_id"]},
        )
    assert second["status"] == "failed" and "dbt não encontrado" in error


def test_invalid_period_does_not_start_a_build():
    with pytest.raises(ValueError, match="Competência inválida"):
        runner.build(period="2025-13")


def test_concurrent_build_is_refused(engine, monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", engine.url.database)
    with engine.connect() as conn:
        conn.execute(sa.text("SELECT pg_advisory_lock(hashtextextended('nyc-mobility:dbt', 0))"))
        try:
            with pytest.raises(RuntimeError, match="Já existe"):
                runner.build()
        finally:
            conn.execute(
                sa.text("SELECT pg_advisory_unlock(hashtextextended('nyc-mobility:dbt', 0))")
            )


@pytest.mark.parametrize("content", ['{"results": []}', '{"results": [null]}', "not json"])
def test_invalid_artifact_is_not_success(tmp_path, content):
    path = tmp_path / "run_results.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        runner.read_results(path)


def test_changed_inputs_cannot_be_reported_as_validated(engine, monkeypatch):
    monkeypatch.setenv("POSTGRES_DB", engine.url.database)

    def dbt(cmd, **kwargs):
        from pathlib import Path

        target = Path(cmd[cmd.index("--target-path") + 1])
        target.joinpath("run_results.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "unique_id": "model.project.trips",
                            "status": "success",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with engine.begin() as conn:
            source_id = conn.scalar(
                sa.text(
                    "INSERT INTO metadata.sources (source, dataset) VALUES ('fixture', 'change') "
                    "RETURNING source_id"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO metadata.ingestion_runs (source_id, period, status) "
                    "VALUES (:source, '2025-01', 'succeeded')"
                ),
                {"source": source_id},
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", dbt)
    try:
        result = runner.build()
        assert result["status"] == "failed" and result["returncode"] != 0
        with engine.connect() as conn:
            assert "insumos mudaram" in conn.scalar(
                sa.text("SELECT error FROM metadata.analytics_runs WHERE run_id = :id"),
                {"id": result["run_id"]},
            )
    finally:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "DELETE FROM metadata.ingestion_runs WHERE source_id IN "
                    "(SELECT source_id FROM metadata.sources "
                    "WHERE source = 'fixture' AND dataset = 'change')"
                )
            )
            conn.execute(
                sa.text(
                    "DELETE FROM metadata.sources WHERE source = 'fixture' AND dataset = 'change'"
                )
            )
