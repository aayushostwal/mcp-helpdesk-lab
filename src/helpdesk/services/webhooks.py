"""Webhook subscriptions and test deliveries."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

from helpdesk.errors import NotFoundError, ValidationFailedError
from helpdesk.models import Webhook, WebhookDelivery
from helpdesk.repositories.base import Page, next_id
from helpdesk.repositories.config import WebhookRepository
from helpdesk.services.base import BaseService, Context, snapshot


def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def assert_target_allowed(url: str, allow_private: bool) -> None:
    """Refuse loopback/private targets (basic SSRF guard) unless explicitly allowed."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise ValidationFailedError("Webhook URL has no host.")
    if allow_private:
        return
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        raise ValidationFailedError(f"Webhook host '{host}' is not allowed.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise ValidationFailedError(f"Webhook address '{host}' is private and not allowed.")


class WebhookService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.webhooks = WebhookRepository(ctx.session)

    async def get(self, webhook_id: str) -> Webhook:
        webhook = await self.webhooks.get(webhook_id)
        if webhook is None:
            raise NotFoundError("Webhook", webhook_id)
        return webhook

    async def list(self) -> Sequence[Webhook]:
        return await self.webhooks.list_all()

    async def create(self, url: str, events: Sequence[str]) -> Webhook:
        assert_target_allowed(url, self.ctx.settings.webhook_allow_private_hosts)
        webhook = Webhook(
            id=await next_id(self.session, "webhook", "WH", 3),
            url=url,
            events=sorted(set(events)),
            secret="whsec_" + secrets.token_urlsafe(24),
            active=True,
            created_at=self.now,
        )
        self.webhooks.add(webhook)
        await self.session.flush()
        self.audit("webhooks.create", "webhook", webhook.id, None, snapshot(webhook))
        await self.commit()
        return webhook

    async def update(self, webhook_id: str, changes: dict[str, Any]) -> Webhook:
        webhook = await self.get(webhook_id)
        if "url" in changes:
            assert_target_allowed(changes["url"], self.ctx.settings.webhook_allow_private_hosts)
        if "events" in changes:
            changes["events"] = sorted(set(changes["events"]))
        before = snapshot(webhook)
        for key, value in changes.items():
            setattr(webhook, key, value)
        self.audit("webhooks.update", "webhook", webhook.id, before, snapshot(webhook))
        await self.commit()
        return webhook

    async def delete(self, webhook_id: str) -> None:
        webhook = await self.get(webhook_id)
        before = snapshot(webhook)
        await self.webhooks.delete(webhook)
        self.audit("webhooks.delete", "webhook", webhook_id, before, None)
        await self.commit()

    async def test(self, webhook_id: str) -> WebhookDelivery:
        """Send a ``webhook.test`` event to the subscriber and record the outcome."""
        webhook = await self.get(webhook_id)
        assert_target_allowed(webhook.url, self.ctx.settings.webhook_allow_private_hosts)
        body = json.dumps(
            {"event": "webhook.test", "webhook_id": webhook.id, "sent_at": self.now.isoformat()}
        ).encode()
        result = await self.ctx.webhook_sender.send(
            webhook.url,
            {
                "Content-Type": "application/json",
                "X-Helpdesk-Event": "webhook.test",
                "X-Helpdesk-Signature": sign_payload(webhook.secret, body),
            },
            body,
            self.ctx.settings.webhook_timeout_seconds,
        )
        delivery = WebhookDelivery(
            webhook_id=webhook.id,
            event="webhook.test",
            success=result.success,
            status_code=result.status_code,
            error=result.error,
            duration_ms=result.duration_ms,
            created_at=self.now,
        )
        self.webhooks.add_delivery(delivery)
        await self.session.flush()
        self.audit(
            "webhooks.test",
            "webhook",
            webhook.id,
            None,
            {"success": result.success, "status_code": result.status_code},
        )
        await self.commit()
        return delivery

    async def list_deliveries(
        self, webhook_id: str, *, limit: int, cursor: str | None
    ) -> Page[WebhookDelivery]:
        await self.get(webhook_id)
        return await self.webhooks.list_deliveries(webhook_id, limit=limit, cursor=cursor)
