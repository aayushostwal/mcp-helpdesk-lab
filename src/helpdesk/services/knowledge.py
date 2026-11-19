"""Knowledge base: search, authoring and publishing."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from helpdesk.errors import ConflictError, NotFoundError
from helpdesk.models import KbArticle
from helpdesk.repositories.base import Page, next_id
from helpdesk.repositories.config import KbRepository
from helpdesk.services.base import BaseService, Context, snapshot


@dataclass(frozen=True)
class SearchHit:
    article: KbArticle
    score: int
    snippet: str


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:120] or "article"


def rank(article: KbArticle, terms: list[str]) -> tuple[int, str]:
    """Score = 3 per term hit in the title + 1 per term hit in the body; plus a body snippet."""
    title, body = article.title.lower(), article.body.lower()
    score = sum(3 * title.count(t) + body.count(t) for t in terms)
    first = min((i for i in (body.find(t) for t in terms) if i >= 0), default=0)
    start = max(0, first - 60)
    snippet = article.body[start : start + 200].replace("\n", " ").strip()
    return score, snippet


class KnowledgeService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.articles = KbRepository(ctx.session)

    async def get(self, article_id: str) -> KbArticle:
        article = await self.articles.get(article_id)
        if article is None:
            raise NotFoundError("Article", article_id)
        return article

    async def list(
        self,
        *,
        q: str | None,
        category: str | None,
        status: str | None,
        limit: int,
        cursor: str | None,
    ) -> Page[KbArticle]:
        return await self.articles.list(
            q=q, category=category, status=status, limit=limit, cursor=cursor
        )

    async def search(
        self, q: str, *, category: str | None, include_drafts: bool, limit: int
    ) -> Sequence[SearchHit]:
        terms = [t for t in re.split(r"\W+", q.lower()) if len(t) > 1]
        if not terms:
            return []
        candidates = await self.articles.search_candidates(
            terms, status=None if include_drafts else "published", category=category, limit=500
        )
        hits = []
        for article in candidates:
            score, snippet = rank(article, terms)
            hits.append(SearchHit(article, score, snippet))
        hits.sort(key=lambda h: (-h.score, h.article.id))
        return hits[:limit]

    async def _unique_slug(self, title: str) -> str:
        base = slugify(title)
        slug, n = base, 2
        while await self.articles.get_by_slug(slug):
            slug, n = f"{base}-{n}", n + 1
        return slug

    async def create(self, data: dict[str, Any]) -> KbArticle:
        principal = self.ctx.principal
        now = self.now
        article = KbArticle(
            id=await next_id(self.session, "kb", "KB", 4),
            slug=await self._unique_slug(data["title"]),
            status="draft",
            author_id=principal.agent_id if principal else None,
            created_at=now,
            updated_at=now,
            **data,
        )
        self.articles.add(article)
        await self.session.flush()
        self.audit("kb.create", "article", article.id, None, snapshot(article))
        await self.commit()
        return article

    async def update(self, article_id: str, changes: dict[str, Any]) -> KbArticle:
        article = await self.get(article_id)
        before = snapshot(article)
        for key, value in changes.items():
            setattr(article, key, value)
        article.updated_at = self.now
        self.audit("kb.update", "article", article.id, before, snapshot(article))
        await self.commit()
        return article

    async def _set_status(self, article_id: str, status: str) -> KbArticle:
        article = await self.get(article_id)
        if article.status == status:
            return article
        before = snapshot(article)
        article.status = status
        article.published_at = self.now if status == "published" else None
        article.updated_at = self.now
        action = "publish" if status == "published" else "unpublish"
        self.audit(f"kb.{action}", "article", article.id, before, snapshot(article))
        await self.commit()
        return article

    async def publish(self, article_id: str) -> KbArticle:
        article = await self.get(article_id)
        if not article.body.strip():
            raise ConflictError("An article with an empty body cannot be published.")
        return await self._set_status(article_id, "published")

    async def unpublish(self, article_id: str) -> KbArticle:
        return await self._set_status(article_id, "draft")

    async def delete(self, article_id: str) -> None:
        article = await self.get(article_id)
        before = snapshot(article)
        await self.articles.delete(article)
        self.audit("kb.delete", "article", article_id, before, None)
        await self.commit()
