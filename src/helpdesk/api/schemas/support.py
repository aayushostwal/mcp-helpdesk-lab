from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, Field

from helpdesk.api.schemas.common import InModel, OutModel
from helpdesk.domain import Channel, Priority, TicketStatus

TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")


def _tag(value: str) -> str:
    value = value.strip().lower()
    if not TAG_RE.match(value):
        raise ValueError("tags are lowercase letters, digits, '-' or '_' (max 49 chars)")
    return value


TagName = Annotated[str, AfterValidator(_tag), Field(examples=["billing"])]


class TicketOut(OutModel):
    id: str = Field(examples=["T-1042"])
    subject: str
    description: str
    status: TicketStatus
    priority: Priority
    channel: Channel
    customer_id: str
    assignee_id: str | None
    team_id: str | None
    order_id: str | None
    tags: list[str]
    escalation_level: int
    reopen_count: int
    merged_into_id: str | None
    csat_score: int | None
    created_at: datetime
    updated_at: datetime
    first_response_at: datetime | None
    solved_at: datetime | None
    closed_at: datetime | None
    first_response_due_at: datetime | None
    resolution_due_at: datetime | None


class TicketCreate(InModel):
    subject: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=20000)
    customer_id: str
    priority: Priority = "normal"
    channel: Channel = "email"
    assignee_id: str | None = None
    team_id: str | None = None
    order_id: str | None = None
    tags: list[TagName] = Field(default_factory=list, max_length=20)


class TicketUpdate(InModel):
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, min_length=1, max_length=20000)
    priority: Priority | None = None
    channel: Channel | None = None
    order_id: str | None = None


class TicketAssign(InModel):
    assignee_id: str | None = Field(
        default=None, description="Agent to assign. Send null to unassign. Omit to leave as is."
    )
    team_id: str | None = Field(
        default=None, description="Team to route to. Send null to clear. Omit to leave as is."
    )


class StatusChange(InModel):
    status: TicketStatus
    note: str | None = Field(
        default=None, max_length=5000, description="Saved as an internal note."
    )


class TicketClose(InModel):
    note: str | None = Field(
        default=None, max_length=5000, description="Closing note, saved as an internal note."
    )


class TicketReopen(InModel):
    reason: str = Field(min_length=1, max_length=2000)


class TicketEscalate(InModel):
    reason: str = Field(min_length=1, max_length=2000)
    team_id: str | None = Field(default=None, description="Optionally route to another team.")


class TicketMerge(InModel):
    target_ticket_id: str = Field(description="The ticket that survives and receives the comments.")


class CsatSubmit(InModel):
    score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class CsatOut(OutModel):
    ticket_id: str
    score: int | None
    comment: str | None
    submitted_at: datetime | None


class CommentOut(OutModel):
    id: str = Field(examples=["CM-00123"])
    ticket_id: str
    author_type: str = Field(description="`agent`, `customer` or `system`.")
    author_id: str | None
    body: str
    is_internal: bool = Field(description="True for internal notes that customers never see.")
    created_at: datetime


class CommentCreate(InModel):
    body: str = Field(min_length=1, max_length=20000)


class TagOut(OutModel):
    name: str
    ticket_count: int = 0
    created_at: datetime


class TagCreate(InModel):
    name: TagName


class TicketTagAdd(InModel):
    tag: TagName
