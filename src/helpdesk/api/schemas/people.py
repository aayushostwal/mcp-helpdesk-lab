from __future__ import annotations

import re
from datetime import datetime

from pydantic import Field, field_validator

from helpdesk.api.schemas.common import InModel, OutModel
from helpdesk.domain import AgentRole, CustomerTier

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL_RE.match(value) or len(value) > 320:
        raise ValueError("must be a valid email address")
    return value


class CustomerOut(OutModel):
    id: str = Field(examples=["C-0311"])
    name: str
    email: str
    phone: str | None
    company: str | None
    tier: str
    status: str = Field(description="`active`, or `merged` if folded into another customer.")
    merged_into_id: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class CustomerCreate(InModel):
    name: str = Field(min_length=1, max_length=200)
    email: str
    phone: str | None = Field(default=None, max_length=50)
    company: str | None = Field(default=None, max_length=200)
    tier: CustomerTier = "standard"
    notes: str | None = Field(default=None, max_length=5000)

    _check_email = field_validator("email")(_email)


class CustomerUpdate(InModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = Field(default=None, max_length=50)
    company: str | None = Field(default=None, max_length=200)
    tier: CustomerTier | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        return _email(value) if value is not None else None


class CustomerMerge(InModel):
    target_customer_id: str = Field(description="The customer that survives the merge.")


class CustomerSummary(OutModel):
    customer: CustomerOut
    ticket_count: int
    open_ticket_count: int = Field(description="Tickets in status new, open or pending.")
    order_count: int


class AgentOut(OutModel):
    id: str = Field(examples=["A-0007"])
    name: str
    email: str
    role: AgentRole
    team_id: str | None
    active: bool
    created_at: datetime


class AgentCreate(InModel):
    name: str = Field(min_length=1, max_length=200)
    email: str
    role: AgentRole = "agent"
    team_id: str | None = None

    _check_email = field_validator("email")(_email)


class AgentUpdate(InModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    role: AgentRole | None = None
    team_id: str | None = None


class TeamOut(OutModel):
    id: str = Field(examples=["TM-002"])
    name: str
    description: str | None
    oncall_agent_id: str | None
    created_at: datetime


class TeamCreate(InModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class TeamMemberAdd(InModel):
    agent_id: str


class OnCallSet(InModel):
    agent_id: str | None = Field(
        description="Agent to put on call. Must be an active member of the team. Null clears it."
    )
