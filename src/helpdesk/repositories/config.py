"""Queries for SLA policies, macros, knowledge base, webhooks and runtime settings."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.models import KbArticle, Macro, Setting, SlaPolicy, Webhook, WebhookDelivery
from helpdesk.repositories.base import Page, like_pattern, paginate


class SlaPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, policy_id: str) -> SlaPolicy | None:
        return await self.session.get(SlaPolicy, policy_id)

    async def get_by_priority(self, priority: str, *, active_only: bool = True) -> SlaPolicy | None:
        stmt = select(SlaPolicy).where(SlaPolicy.priority == priority)
        if active_only:
            stmt = stmt.where(SlaPolicy.active.is_(True))
        return await self.session.scalar(stmt)

    def add(self, policy: SlaPolicy) -> None:
        self.session.add(policy)

    async def list_all(self) -> Sequence[SlaPolicy]:
        return (await self.session.scalars(select(SlaPolicy).order_by(SlaPolicy.id))).all()


class MacroRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, macro_id: str) -> Macro | None:
        return await self.session.get(Macro, macro_id)

    async def get_by_name(self, name: str) -> Macro | None:
        return await self.session.scalar(select(Macro).where(Macro.name == name))

    def add(self, macro: Macro) -> None:
        self.session.add(macro)

    async def delete(self, macro: Macro) -> None:
        await self.session.delete(macro)

    async def list(self, *, q: str | None, limit: int, cursor: str | None) -> Page[Macro]:
        stmt = select(Macro)
        if q:
            pattern = like_pattern(q)
            stmt = stmt.where(
                or_(Macro.name.ilike(pattern, escape="\\"), Macro.body.ilike(pattern, escape="\\"))
            )
        return await paginate(
            self.session,
            stmt,
            sort=Macro.id,
            id_col=Macro.id,
            limit=limit,
            cursor=cursor,
            descending=False,
        )


class KbRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, article_id: str) -> KbArticle | None:
        return await self.session.get(KbArticle, article_id)

    async def get_by_slug(self, slug: str) -> KbArticle | None:
        return await self.session.scalar(select(KbArticle).where(KbArticle.slug == slug))

    def add(self, article: KbArticle) -> None:
        self.session.add(article)

    async def delete(self, article: KbArticle) -> None:
        await self.session.delete(article)

    async def list(
        self,
        *,
        q: str | None,
        category: str | None,
        status: str | None,
        limit: int,
        cursor: str | None,
    ) -> Page[KbArticle]:
        stmt = select(KbArticle)
        if q:
            pattern = like_pattern(q)
            stmt = stmt.where(
                or_(
                    KbArticle.title.ilike(pattern, escape="\\"),
                    KbArticle.body.ilike(pattern, escape="\\"),
                )
            )
        if category:
            stmt = stmt.where(KbArticle.category == category)
        if status:
            stmt = stmt.where(KbArticle.status == status)
        return await paginate(
            self.session,
            stmt,
            sort=KbArticle.updated_at,
            id_col=KbArticle.id,
            limit=limit,
            cursor=cursor,
        )

    async def search_candidates(
        self, terms: Sequence[str], *, status: str | None, category: str | None, limit: int
    ) -> Sequence[KbArticle]:
        """Articles matching any term in title or body; ranking happens in the service."""
        clauses = []
        for term in terms:
            pattern = like_pattern(term)
            clauses.append(KbArticle.title.ilike(pattern, escape="\\"))
            clauses.append(KbArticle.body.ilike(pattern, escape="\\"))
        stmt = select(KbArticle).where(or_(*clauses))
        if status:
            stmt = stmt.where(KbArticle.status == status)
        if category:
            stmt = stmt.where(KbArticle.category == category)
        return (await self.session.scalars(stmt.limit(limit))).all()


class WebhookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, webhook_id: str) -> Webhook | None:
        return await self.session.get(Webhook, webhook_id)

    def add(self, webhook: Webhook) -> None:
        self.session.add(webhook)

    async def delete(self, webhook: Webhook) -> None:
        await self.session.execute(
            delete(WebhookDelivery).where(WebhookDelivery.webhook_id == webhook.id)
        )
        await self.session.delete(webhook)

    async def list_all(self) -> Sequence[Webhook]:
        return (await self.session.scalars(select(Webhook).order_by(Webhook.id))).all()

    def add_delivery(self, delivery: WebhookDelivery) -> None:
        self.session.add(delivery)

    async def list_deliveries(
        self, webhook_id: str, *, limit: int, cursor: str | None
    ) -> Page[WebhookDelivery]:
        stmt = select(WebhookDelivery).where(WebhookDelivery.webhook_id == webhook_id)
        return await paginate(
            self.session,
            stmt,
            sort=WebhookDelivery.created_at,
            id_col=WebhookDelivery.id,
            limit=limit,
            cursor=cursor,
        )


class SettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def all(self) -> dict[str, Any]:
        rows = (await self.session.scalars(select(Setting))).all()
        return {row.key: row.value for row in rows}

    async def get_row(self, key: str) -> Setting | None:
        return await self.session.get(Setting, key)

    def add(self, row: Setting) -> None:
        self.session.add(row)
