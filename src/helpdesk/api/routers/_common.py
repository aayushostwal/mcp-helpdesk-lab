"""Helpers shared by routers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from helpdesk.api.schemas.common import Paginated, ProblemDetail
from helpdesk.repositories.base import Page

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetail, "description": "Missing, invalid, revoked or expired API key."},
    403: {"model": ProblemDetail, "description": "The key lacks a required scope or agent role."},
    404: {"model": ProblemDetail, "description": "The referenced resource does not exist."},
    409: {"model": ProblemDetail, "description": "The request conflicts with current state."},
    422: {"model": ProblemDetail, "description": "Invalid input or a business rule forbids it."},
}


def to_page[M: BaseModel](page: Page[Any], schema: type[M]) -> Paginated[M]:
    return Paginated[M](
        items=[schema.model_validate(item) for item in page.items], next_cursor=page.next_cursor
    )
