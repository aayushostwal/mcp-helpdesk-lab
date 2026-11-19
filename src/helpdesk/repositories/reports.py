"""Aggregate queries for reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.models import Agent, Ticket
from helpdesk.models.base import UTCDateTime

ACTIVE_STATUSES = ("new", "open", "pending")


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _range[S: Select[*tuple[Any, ...]]](
        stmt: S, start: datetime | None, end: datetime | None
    ) -> S:
        if start:
            stmt = stmt.where(Ticket.created_at >= start)
        if end:
            stmt = stmt.where(Ticket.created_at < end)
        return stmt

    async def ticket_volume(
        self, group_by: str, start: datetime | None, end: datetime | None
    ) -> list[tuple[str, int]]:
        group_expr: Any
        if group_by == "day":
            group_expr = func.date(Ticket.created_at)
        elif group_by == "status":
            group_expr = Ticket.status
        elif group_by == "priority":
            group_expr = Ticket.priority
        else:
            group_expr = func.coalesce(Ticket.team_id, "unassigned")
        stmt = self._range(select(group_expr, func.count()), start, end)
        stmt = stmt.group_by(group_expr).order_by(group_expr)
        return [(str(key), int(count)) for key, count in (await self.session.execute(stmt)).all()]

    async def sla_compliance(
        self, now: datetime, start: datetime | None, end: datetime | None
    ) -> list[dict[str, Any]]:
        now_lit = literal(now, UTCDateTime())
        fr_has = Ticket.first_response_due_at.is_not(None)
        fr_breach = fr_has & (
            Ticket.first_response_due_at < func.coalesce(Ticket.first_response_at, now_lit)
        )
        res_has = Ticket.resolution_due_at.is_not(None)
        res_breach = res_has & (Ticket.resolution_due_at < func.coalesce(Ticket.solved_at, now_lit))
        stmt = self._range(
            select(
                Ticket.priority,
                func.count(),
                func.sum(case((fr_has, 1), else_=0)),
                func.sum(case((fr_breach, 1), else_=0)),
                func.sum(case((res_has, 1), else_=0)),
                func.sum(case((res_breach, 1), else_=0)),
            ),
            start,
            end,
        ).group_by(Ticket.priority)
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "priority": priority,
                "tickets": int(total),
                "first_response_tracked": int(fr_total or 0),
                "first_response_breached": int(fr_bad or 0),
                "resolution_tracked": int(res_total or 0),
                "resolution_breached": int(res_bad or 0),
            }
            for priority, total, fr_total, fr_bad, res_total, res_bad in rows
        ]

    async def agent_workload(self) -> list[dict[str, Any]]:
        stmt = (
            select(Agent.id, Agent.name, Agent.team_id, Ticket.status, func.count(Ticket.id))
            .join(Ticket, Ticket.assignee_id == Agent.id)
            .where(Ticket.status.in_(ACTIVE_STATUSES))
            .group_by(Agent.id, Agent.name, Agent.team_id, Ticket.status)
            .order_by(Agent.id)
        )
        rows = (await self.session.execute(stmt)).all()
        by_agent: dict[str, dict[str, Any]] = {}
        for agent_id, name, team_id, status, count in rows:
            entry = by_agent.setdefault(
                agent_id,
                {"agent_id": agent_id, "agent_name": name, "team_id": team_id, "by_status": {}},
            )
            entry["by_status"][status] = int(count)
        return list(by_agent.values())

    async def csat(self, start: datetime | None, end: datetime | None) -> list[tuple[int, int]]:
        stmt = select(Ticket.csat_score, func.count()).where(Ticket.csat_score.is_not(None))
        stmt = self._range(stmt, start, end).group_by(Ticket.csat_score)
        return [
            (int(score), int(count))
            for score, count in (await self.session.execute(stmt)).all()
            if score is not None
        ]

    async def backlog(self) -> list[tuple[str, str, int]]:
        stmt = (
            select(Ticket.status, Ticket.priority, func.count())
            .where(Ticket.status.in_(ACTIVE_STATUSES))
            .group_by(Ticket.status, Ticket.priority)
            .order_by(Ticket.status, Ticket.priority)
        )
        return [(str(s), str(p), int(c)) for s, p, c in (await self.session.execute(stmt)).all()]
