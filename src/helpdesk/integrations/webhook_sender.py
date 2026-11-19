"""Outbound webhook delivery."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    status_code: int | None
    error: str | None
    duration_ms: int


class WebhookSender(Protocol):
    async def send(
        self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> DeliveryResult: ...


class HttpxWebhookSender:
    """POSTs the payload with httpx. Redirects are not followed."""

    async def send(
        self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> DeliveryResult:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
                response = await client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return DeliveryResult(False, None, f"{type(exc).__name__}: {exc}"[:500], elapsed)
        elapsed = int((time.perf_counter() - started) * 1000)
        ok = 200 <= response.status_code < 300
        error = None if ok else f"Receiver answered HTTP {response.status_code}"
        return DeliveryResult(ok, response.status_code, error, elapsed)


class SimulatedWebhookSender:
    """Never touches the network; always reports success. Used for demos and benchmarks."""

    async def send(
        self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> DeliveryResult:
        return DeliveryResult(True, 200, None, 0)
