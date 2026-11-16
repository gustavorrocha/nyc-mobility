from datetime import UTC, datetime
from http.server import HTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from dashboard import metrics


def test_publication_timestamp_is_exposed_as_unix_seconds():
    published = datetime(2025, 1, 1, tzinfo=UTC)
    with patch.object(metrics, "query", side_effect=[[{"published_at": published}], []]):
        result = metrics.metrics()
    assert "# TYPE nyc_mobility_publication_timestamp_seconds gauge" in result
    assert "nyc_mobility_publication_timestamp_seconds 1735689600.0\n" in result


def test_no_publication_does_not_invent_a_timestamp():
    with patch.object(metrics, "query", return_value=[]):
        assert not any(
            line.startswith(metrics.METRIC + " ") for line in metrics.metrics().splitlines()
        )


@pytest.mark.parametrize(("status", "value"), [("succeeded", 1), ("failed", 0)])
def test_last_completed_build_metrics(status, value):
    with patch.object(metrics, "query", side_effect=[[], [{"status": status, "seconds": 12.5}]]):
        result = metrics.metrics()
    assert f"nyc_mobility_build_success {value}\n" in result
    assert "nyc_mobility_build_duration_seconds 12.5\n" in result


def test_no_completed_build_has_no_success_or_duration_sample():
    with patch.object(metrics, "query", return_value=[]):
        result = metrics.metrics()
    assert "nyc_mobility_build_success" not in result
    assert "nyc_mobility_build_duration_seconds" not in result


def test_database_error_is_not_reported_as_success():
    server = HTTPServer(("127.0.0.1", 0), metrics.Handler)
    thread = Thread(target=server.handle_request)
    with patch.object(metrics, "query", side_effect=RuntimeError("database error")):
        thread.start()
        try:
            with pytest.raises(HTTPError) as exc:
                urlopen(f"http://127.0.0.1:{server.server_port}/metrics", timeout=5)
            assert exc.value.code == 503
        finally:
            thread.join(timeout=5)
            server.server_close()
