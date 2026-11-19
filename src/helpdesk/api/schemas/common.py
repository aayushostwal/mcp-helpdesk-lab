"""Types shared by every schema module."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _format_money(value: Decimal) -> str:
    return f"{value:.2f}"


Money = Annotated[
    Decimal,
    PlainSerializer(_format_money, return_type=str, when_used="json"),
    Field(description="Decimal amount as a string with two decimals, e.g. '49.90'."),
]


class OutModel(BaseModel):
    """Base for response models built from ORM objects."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class InModel(BaseModel):
    """Base for request bodies. Unknown fields are rejected so typos surface as 422s."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Paginated[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(
        default=None,
        description="Pass as `cursor` to fetch the next page. Null when there are no more items.",
    )


class ProblemDetail(BaseModel):
    """RFC 9457 problem details."""

    type: str = Field(description="Stable URI identifying the problem type.")
    title: str
    status: int
    detail: str
    instance: str | None = Field(default=None, description="The request path.")
    request_id: str | None = Field(default=None, description="Matches the X-Request-ID header.")
    errors: list[dict[str, Any]] | None = Field(
        default=None, description="Field-level validation errors (422 only)."
    )

    model_config = ConfigDict(extra="allow")
