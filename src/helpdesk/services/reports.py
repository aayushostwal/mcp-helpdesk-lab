"""Read-only aggregate reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from helpdesk.repositories.reports import ReportRepository
from helpdesk.services.base import BaseService, Context


def _pct(part: int, whole: int) -> float | None:
    return round(100 * (whole - part) / whole, 1) if whole else None


class ReportService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.reports = ReportRepository(ctx.session)

    async def ticket_volume(
        self, group_by: str, start: datetime | None, end: datetime | None
    ) -> dict[str, Any]:
        rows = await self.reports.ticket_volume(group_by, start, end)
        return {
            "group_by": group_by,
            "start": start,
            "end": end,
            "total": sum(count for _, count in rows),
            "buckets": [{"key": key, "count": count} for key, count in rows],
        }

    async def sla_compliance(self, start: datetime | None, end: datetime | None) -> dict[str, Any]:
        rows = await self.reports.sla_compliance(self.now, start, end)
        for row in rows:
            row["first_response_compliance_pct"] = _pct(
                row["first_response_breached"], row["first_response_tracked"]
            )
            row["resolution_compliance_pct"] = _pct(
                row["resolution_breached"], row["resolution_tracked"]
            )
        total_fr = sum(r["first_response_tracked"] for r in rows)
        bad_fr = sum(r["first_response_breached"] for r in rows)
        total_res = sum(r["resolution_tracked"] for r in rows)
        bad_res = sum(r["resolution_breached"] for r in rows)
        return {
            "start": start,
            "end": end,
            "evaluated_at": self.now,
            "by_priority": sorted(rows, key=lambda r: r["priority"]),
            "overall_first_response_compliance_pct": _pct(bad_fr, total_fr),
            "overall_resolution_compliance_pct": _pct(bad_res, total_res),
        }

    async def agent_workload(self) -> dict[str, Any]:
        rows = await self.reports.agent_workload()
        for row in rows:
            row["active_tickets"] = sum(row["by_status"].values())
        rows.sort(key=lambda r: (-r["active_tickets"], r["agent_id"]))
        return {"agents": rows, "total_active_tickets": sum(r["active_tickets"] for r in rows)}

    async def csat_summary(self, start: datetime | None, end: datetime | None) -> dict[str, Any]:
        rows = await self.reports.csat(start, end)
        responses = sum(count for _, count in rows)
        total = sum(score * count for score, count in rows)
        return {
            "start": start,
            "end": end,
            "responses": responses,
            "average_score": round(total / responses, 2) if responses else None,
            "distribution": {str(score): count for score, count in sorted(rows)},
        }

    async def backlog(self) -> dict[str, Any]:
        rows = await self.reports.backlog()
        return {
            "total_active": sum(c for _, _, c in rows),
            "cells": [{"status": s, "priority": p, "count": c} for s, p, c in rows],
        }
