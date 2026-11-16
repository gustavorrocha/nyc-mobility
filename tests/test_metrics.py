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
    with patch.object(metrics, "query", return_value=[{"published_at": published}]):
        result = metrics.metrics()
    assert "# TYPE nyc_mobility_publication_timestamp_seconds gauge" in result
    assert "nyc_mobility_publication_timestamp_seconds 1735689600.0\n" in result


def test_no_publication_does_not_invent_a_timestamp():
    with patch.object(metrics, "query", return_value=[]):
        assert not any(
            line.startswith(metrics.METRIC + " ") for line in metrics.metrics().splitlines()
        )


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
