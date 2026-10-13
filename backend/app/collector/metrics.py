"""Counters for the collector, a Prometheus text endpoint and a health check (stdlib only)."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Metrics:
    def __init__(self, poll_interval_s: float):
        self.poll_interval_s = poll_interval_s
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.counters: dict[str, float] = {
            "polls_ok_total": 0,
            "polls_failed_total": 0,
            "observations_written_total": 0,
            "arrival_events_written_total": 0,
            "duplicates_skipped_total": 0,
            "alerts_polls_failed_total": 0,
        }
        self.gauges: dict[str, float] = {
            "vehicles_in_feed": 0,
            "matched_share": 0,
            "last_poll_duration_seconds": 0,
            "feed_age_seconds": 0,
            "alerts_active": 0,
        }
        self.last_success: float | None = None

    def count(self, name: str, amount: float = 1) -> None:
        with self.lock:
            self.counters[name] += amount

    def gauge(self, name: str, value: float) -> None:
        with self.lock:
            self.gauges[name] = value

    def success(self) -> None:
        with self.lock:
            self.last_success = time.monotonic()

    def healthy(self) -> bool:
        """True while the last successful poll is younger than three poll intervals."""
        with self.lock:
            reference = self.last_success if self.last_success is not None else self.started
            grace = 3 * self.poll_interval_s + (60 if self.last_success is None else 0)
            return time.monotonic() - reference <= grace

    def render(self) -> str:
        with self.lock:
            lines = [f"reroute_collector_{name} {value}" for name, value in self.counters.items()]
            lines += [f"reroute_collector_{name} {value}" for name, value in self.gauges.items()]
            since = time.monotonic() - self.last_success if self.last_success is not None else -1
        lines.append(f"reroute_collector_seconds_since_last_success {since:.1f}")
        return "\n".join(lines) + "\n"


def serve(metrics: Metrics, port: int) -> ThreadingHTTPServer:
    """Serve /healthz and /metrics on a background thread."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server API
            if self.path == "/healthz":
                ok = metrics.healthy()
                body, status = (b"ok\n", 200) if ok else (b"stale\n", 503)
            elif self.path == "/metrics":
                body, status = metrics.render().encode(), 200
            else:
                body, status = b"not found\n", 404
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="metrics").start()
    return server
