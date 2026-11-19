"""Prometheus metrics. A dedicated registry keeps tests and multiple apps isolated."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "helpdesk_http_requests_total",
            "HTTP requests by method, route template and status code.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "helpdesk_http_request_duration_seconds",
            "HTTP request latency by method and route template.",
            ["method", "route"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
            registry=self.registry,
        )

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.labels(method, route, str(status)).inc()
        self.latency.labels(method, route).observe(seconds)

    def render(self) -> bytes:
        return generate_latest(self.registry)
