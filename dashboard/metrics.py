"""Exporta a data da última publicação validada para o Prometheus."""

import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

from dashboard.db import query, snapshot

METRIC = "nyc_mobility_publication_timestamp_seconds"


@snapshot()
def metrics() -> str:
    rows = query("SELECT published_at FROM metadata.analytics_publication")
    result = (
        f"# HELP {METRIC} Unix timestamp da última publicação validada.\n# TYPE {METRIC} gauge\n"
    )
    if rows:
        result += f"{METRIC} {rows[0]['published_at'].timestamp()}\n"
    builds = query(
        "SELECT status, extract(epoch FROM finished_at - started_at)::float8 AS seconds "
        "FROM metadata.analytics_runs WHERE status IN ('succeeded', 'failed') "
        "AND finished_at IS NOT NULL ORDER BY run_id DESC LIMIT 1"
    )
    if builds:
        build = builds[0]
        for name, value, help_text in (
            (
                "nyc_mobility_build_success",
                int(build["status"] == "succeeded"),
                "Last completed build: 1 succeeded, 0 failed.",
            ),
            (
                "nyc_mobility_build_duration_seconds",
                build["seconds"],
                "Duration of the last completed build in seconds.",
            ),
        ):
            result += f"# HELP {name} {help_text}\n# TYPE {name} gauge\n{name} {value}\n"
    return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        try:
            body = metrics().encode("utf-8")
        except Exception:
            logging.exception("Falha ao consultar publicação")
            self.send_error(503, "Banco indisponível")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9101), Handler).serve_forever()
