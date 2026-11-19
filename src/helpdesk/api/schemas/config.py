from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from helpdesk.api.schemas.common import InModel, Money, OutModel
from helpdesk.api.schemas.support import TagName
from helpdesk.domain import ArticleStatus, Priority, WebhookEvent


class TargetStatusOut(OutModel):
    due_at: datetime | None
    achieved_at: datetime | None
    state: str = Field(description="`untracked`, `met`, `breached`, `pending` or `at_risk`.")
    minutes_remaining: int | None = Field(description="Negative when overdue.")


class SlaEvaluationOut(OutModel):
    ticket_id: str
    priority: Priority
    policy_id: str | None
    evaluated_at: datetime
    first_response: TargetStatusOut
    resolution: TargetStatusOut
    breached: bool


class SlaPolicyOut(OutModel):
    id: str = Field(examples=["SLA-002"])
    name: str
    priority: Priority
    first_response_minutes: int
    resolution_minutes: int
    active: bool
    created_at: datetime


class SlaPolicyCreate(InModel):
    name: str = Field(min_length=1, max_length=100)
    priority: Priority
    first_response_minutes: int = Field(ge=1, le=525600)
    resolution_minutes: int = Field(ge=1, le=525600)


class SlaPolicyUpdate(InModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    first_response_minutes: int | None = Field(default=None, ge=1, le=525600)
    resolution_minutes: int | None = Field(default=None, ge=1, le=525600)
    active: bool | None = None


class MacroActions(InModel):
    status: str | None = Field(default=None, description="Move the ticket to this status.")
    priority: Priority | None = None
    add_tags: list[TagName] = Field(default_factory=list)
    public: bool = Field(
        default=True, description="Post as a public reply (false = internal note)."
    )


class MacroOut(OutModel):
    id: str = Field(examples=["M-004"])
    name: str
    description: str | None
    body: str = Field(description="Text with {{customer_name}}, {{ticket_id}}, {{agent_name}}.")
    actions: dict[str, Any]
    created_at: datetime


class MacroCreate(InModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    body: str = Field(min_length=1, max_length=10000)
    actions: MacroActions = Field(default_factory=MacroActions)


class MacroUpdate(InModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    body: str | None = Field(default=None, min_length=1, max_length=10000)
    actions: MacroActions | None = None


class ArticleOut(OutModel):
    id: str = Field(examples=["KB-0012"])
    title: str
    slug: str
    category: str
    body: str
    status: ArticleStatus
    author_id: str | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class ArticleHit(OutModel):
    id: str
    title: str
    slug: str
    category: str
    status: ArticleStatus
    score: int = Field(description="Relevance: 3 per title match + 1 per body match.")
    snippet: str


class ArticleCreate(InModel):
    title: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=100000)


class ArticleUpdate(InModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    body: str | None = Field(default=None, min_length=1, max_length=100000)


class WebhookOut(OutModel):
    id: str = Field(examples=["WH-003"])
    url: str
    events: list[str]
    active: bool
    created_at: datetime


class WebhookCreated(WebhookOut):
    secret: str = Field(description="Signing secret. Shown only in this response.")


class WebhookCreate(InModel):
    url: str = Field(pattern=r"^https?://\S+$", max_length=2000)
    events: list[WebhookEvent] = Field(min_length=1)


class WebhookUpdate(InModel):
    url: str | None = Field(default=None, pattern=r"^https?://\S+$", max_length=2000)
    events: list[WebhookEvent] | None = Field(default=None, min_length=1)
    active: bool | None = None

    @field_validator("events")
    @classmethod
    def _not_null(cls, value: list[str] | None) -> list[str] | None:
        return value


class DeliveryOut(OutModel):
    id: int
    webhook_id: str
    event: str
    success: bool
    status_code: int | None
    error: str | None
    duration_ms: int
    created_at: datetime


class SettingsOut(OutModel):
    refund_window_days: int
    refund_approval_threshold: Money
    reopen_window_days: int
    overridden: list[str] = Field(description="Settings changed from their environment default.")


class SettingsUpdate(InModel):
    refund_window_days: int | None = Field(default=None, ge=1, le=365)
    refund_approval_threshold: Decimal | None = Field(
        default=None, gt=0, max_digits=10, decimal_places=2
    )
    reopen_window_days: int | None = Field(default=None, ge=1, le=365)
