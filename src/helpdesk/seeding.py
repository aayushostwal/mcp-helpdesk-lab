"""Deterministic synthetic data.

``build_dataset`` is pure: the same ``(seed, as_of)`` always produces the same rows, because every
random choice comes from one seeded ``random.Random`` plus a seeded Faker. Nothing reads the
wall clock. ``seed_database`` writes the rows; ``fingerprint`` hashes table contents so tests can
prove two seeded databases are identical.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from faker import Faker
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.models import (
    Agent,
    Base,
    Comment,
    Customer,
    KbArticle,
    Macro,
    Order,
    OrderItem,
    Refund,
    Sequence,
    SlaPolicy,
    Tag,
    Team,
    Ticket,
    Webhook,
)
from helpdesk.models.base import cents
from helpdesk.services.sla import compute_due_dates, evaluate_ticket

DEFAULT_SEED = 42
DEFAULT_AS_OF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

TEAM_NAMES = [
    ("Billing", "Invoices, charges and payment problems."),
    ("Technical Support", "Product bugs, logins and how-to questions."),
    ("Shipping and Logistics", "Delivery status, lost and damaged parcels."),
    ("Account Management", "VIP accounts, renewals and plan changes."),
    ("Escalations", "Senior team for urgent and sensitive cases."),
]
N_CUSTOMERS = 500
N_AGENTS = 40
N_TICKETS = 2000
N_ORDERS = 1500
SLA_TARGETS = {"urgent": (30, 240), "high": (60, 480), "normal": (240, 1440), "low": (480, 4320)}

TOPICS: dict[str, list[tuple[str, str]]] = {
    "billing": [
        ("I was charged twice for order {order}", "My card shows two charges for the same order."),
        (
            "Invoice for order {order} shows the wrong amount",
            "The total on the invoice is not what I paid.",
        ),
        (
            "Please update the billing address on my account",
            "We moved offices and need invoices re-issued.",
        ),
    ],
    "shipping": [
        ("Where is my order {order}?", "Tracking has not updated for several days."),
        (
            "Order {order} marked delivered but nothing arrived",
            "The carrier says delivered, I received nothing.",
        ),
    ],
    "damaged": [
        (
            "Item from order {order} arrived damaged",
            "The box was crushed and the product is broken.",
        ),
        (
            "Wrong item received in order {order}",
            "I ordered a different model than the one in the box.",
        ),
    ],
    "refund": [
        ("Refund request for order {order}", "I would like my money back for this order."),
        (
            "Refund for order {order} has not arrived",
            "It has been a week since I was told it was approved.",
        ),
    ],
    "login": [
        ("Cannot log in after password reset", "The reset link works but I am still rejected."),
        ("Two-factor codes are not accepted", "My authenticator codes keep failing."),
    ],
    "bug": [
        ("App crashes when exporting a report", "It closes right after I press export."),
        ("Dashboard numbers do not match the CSV export", "Totals differ by a few percent."),
    ],
    "how-to": [
        ("How do I add a teammate to my plan?", "I could not find where to invite people."),
        ("How can I change my notification settings?", "I get too many emails."),
    ],
    "outage": [
        ("Service unavailable since this morning", "Every page returns an error for our team.")
    ],
    "feature-request": [("Please add dark mode", "It would help during night shifts.")],
}
ORDER_TOPICS = ("billing", "shipping", "damaged", "refund")
TOPIC_TEAM = {
    "billing": 0,
    "refund": 0,
    "shipping": 2,
    "damaged": 2,
    "login": 1,
    "bug": 1,
    "how-to": 1,
    "outage": 4,
    "feature-request": 3,
}
CUSTOMER_LINES = [
    "Thanks for looking into this, it is quite urgent for us.",
    "Any update on this? I have not heard back.",
    "I have attached the details you asked for.",
    "That solved it, thank you.",
]
AGENT_LINES = [
    "Thanks for reaching out. I am looking into this now.",
    "I have checked your account and escalated the details to the right team.",
    "Could you confirm the email address on the order so I can verify it?",
    "This should be fixed on our side. Please try again and let me know.",
    "I am sorry for the trouble. We have applied a correction.",
]
NOTE_LINES = [
    "Verified customer identity by email.",
    "Waiting on carrier confirmation, follow up tomorrow.",
    "Customer is on a plus plan, prioritise.",
    "Duplicate of an earlier report, linked for context.",
]
PRODUCT_NOUNS = [
    "Desk Lamp",
    "Ergonomic Chair",
    "Standing Desk",
    "Monitor Arm",
    "Keyboard",
    "Mouse",
    "Webcam",
    "Headset",
    "USB Hub",
    "Laptop Stand",
    "Notebook Set",
    "Cable Kit",
    "Docking Station",
    "Whiteboard",
    "Desk Mat",
    "Footrest",
    "Speaker",
    "Router",
    "Backpack",
    "Water Bottle",
]
ARTICLES = [
    (
        "How to reset your password",
        "account",
        "Open the sign-in page and choose 'Forgot password'. We email a reset link that is valid for one hour. If nothing arrives, check spam and confirm the address on file.",
    ),
    (
        "Refund policy",
        "billing",
        "Orders can be refunded within 30 days of delivery. Refunds above 500.00 need approval from a team lead. Refunds go back to the original payment method in 5-10 days.",
    ),
    (
        "Track your order",
        "shipping",
        "Use the tracking link in your shipping email. Tracking can lag by 24 hours after dispatch. If it has not moved for 5 days, contact support.",
    ),
    (
        "Change your billing address",
        "billing",
        "Go to Settings, then Billing, and edit the address. Past invoices keep the old address; ask support to re-issue them.",
    ),
    (
        "Set up two-factor authentication",
        "account",
        "Enable two-factor in Security settings and scan the QR code with an authenticator app. Save the recovery codes.",
    ),
    (
        "Export a report to CSV",
        "product",
        "Open the report, choose Export, then CSV. Large reports are emailed when ready.",
    ),
    (
        "What to do when an item arrives damaged",
        "shipping",
        "Take photos of the box and the product within 7 days and reply to your order email. We replace or refund damaged items.",
    ),
    (
        "Understanding your invoice",
        "billing",
        "Invoices list line items, tax and discounts. The total matches the charge on your card.",
    ),
    (
        "Add teammates to your plan",
        "product",
        "Plan owners can invite teammates from Settings, then Team. Each seat is billed monthly.",
    ),
    (
        "Notification settings",
        "product",
        "Choose which emails you receive under Settings, then Notifications.",
    ),
    (
        "Service status and outages",
        "product",
        "Check the status page for incidents. Subscribe to get updates by email.",
    ),
    (
        "Shipping times and carriers",
        "shipping",
        "Standard shipping takes 3-5 business days. Express takes 1-2 business days.",
    ),
    (
        "Cancel an order",
        "shipping",
        "Orders can be cancelled until they ship. After that, request a refund once delivered.",
    ),
    (
        "Update your email address",
        "account",
        "Change your email in Account settings. We send a confirmation link to the new address.",
    ),
    (
        "Delete your account",
        "account",
        "Account deletion is permanent. Export your data first from Settings.",
    ),
]
MACROS = [
    (
        "Acknowledge",
        "Hi {{customer_name}}, thanks for contacting us about {{ticket_id}}. I am on it. {{agent_name}}",
        {"status": "open", "public": True},
    ),
    (
        "Ask for order number",
        "Hi {{customer_name}}, could you share your order number so I can look this up? {{agent_name}}",
        {"status": "pending", "public": True},
    ),
    (
        "Refund issued",
        "Hi {{customer_name}}, your refund has been issued and should arrive in 5-10 days. {{agent_name}}",
        {"add_tags": ["refund"], "public": True},
    ),
    (
        "Escalation note",
        "Escalating {{ticket_id}} to the senior team for review.",
        {"priority": "high", "public": False},
    ),
    (
        "Password reset steps",
        "Hi {{customer_name}}, please use the 'Forgot password' link on the sign-in page. {{agent_name}}",
        {"add_tags": ["login"], "public": True},
    ),
    (
        "Shipping delay apology",
        "Hi {{customer_name}}, sorry for the delay with your delivery. We are chasing the carrier. {{agent_name}}",
        {"add_tags": ["shipping"], "public": True},
    ),
    (
        "Solved: follow-up",
        "Hi {{customer_name}}, we believe this is resolved. Reply if you need anything else. {{agent_name}}",
        {"status": "solved", "public": True},
    ),
    (
        "Damaged item instructions",
        "Hi {{customer_name}}, please send photos of the damage and we will arrange a replacement. {{agent_name}}",
        {"add_tags": ["damaged"], "public": True},
    ),
    (
        "Request more information",
        "Hi {{customer_name}}, we need a little more detail to continue. {{agent_name}}",
        {"status": "pending", "public": True},
    ),
    ("Internal handover", "Handover for {{ticket_id}}: see previous notes.", {"public": False}),
    (
        "Billing correction",
        "Hi {{customer_name}}, we corrected the charge and you will see it within a week. {{agent_name}}",
        {"add_tags": ["billing"], "public": True},
    ),
    (
        "Feature request logged",
        "Hi {{customer_name}}, thanks, we logged your request with the product team. {{agent_name}}",
        {"add_tags": ["feature-request"], "public": True},
    ),
]


@dataclass
class Dataset:
    seed: int
    as_of: datetime
    teams: list[Team] = field(default_factory=list)
    agents: list[Agent] = field(default_factory=list)
    customers: list[Customer] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    policies: list[SlaPolicy] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    tickets: list[Ticket] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    articles: list[KbArticle] = field(default_factory=list)
    macros: list[Macro] = field(default_factory=list)
    webhooks: list[Webhook] = field(default_factory=list)
    fixtures: dict[str, Any] = field(default_factory=dict)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".")


class _Builder:
    def __init__(self, seed: int, as_of: datetime) -> None:
        self.rng = random.Random(seed)
        self.fake = Faker("en_US")
        self.fake.seed_instance(seed)
        self.ds = Dataset(seed=seed, as_of=as_of)
        self.as_of = as_of
        self.policy_by_priority: dict[str, SlaPolicy] = {}
        self.tag_by_name: dict[str, Tag] = {}
        self.catalog: list[tuple[str, str, int]] = []
        self.n_comment = 0
        self.n_order = 0
        self.n_refund = 0
        self.n_ticket = 0

    # ----------------------------------------------------------------- helpers
    def ago(self, days: float) -> datetime:
        return self.as_of - timedelta(days=days)

    def weighted(self, options: dict[str, float]) -> str:
        return self.rng.choices(list(options), weights=list(options.values()))[0]

    # ----------------------------------------------------------------- reference
    def build_reference(self) -> None:
        ds = self.ds
        for i, (name, desc) in enumerate(TEAM_NAMES, start=1):
            ds.teams.append(
                Team(id=f"TM-{i:03d}", name=name, description=desc, created_at=self.ago(800))
            )
        for i in range(1, N_AGENTS + 1):
            team = ds.teams[(i - 1) % len(ds.teams)]
            name = self.fake.name()
            role = "admin" if i == 1 else ("lead" if i <= 6 else "agent")
            ds.agents.append(
                Agent(
                    id=f"A-{i:04d}",
                    name=name,
                    email=f"{_slug(name)}.{i}@helpdesk.example.com",
                    role=role,
                    team_id=team.id,
                    active=i not in (38, 39, 40),
                    created_at=self.ago(self.rng.uniform(300, 780)),
                )
            )
        for team in ds.teams:
            members = [a for a in ds.agents if a.team_id == team.id and a.active]
            team.oncall_agent_id = members[0].id
        for i, (priority, (first, resolution)) in enumerate(SLA_TARGETS.items(), start=1):
            policy = SlaPolicy(
                id=f"SLA-{i:03d}",
                name=f"{priority.capitalize()} priority",
                priority=priority,
                first_response_minutes=first,
                resolution_minutes=resolution,
                active=True,
                created_at=self.ago(800),
            )
            ds.policies.append(policy)
            self.policy_by_priority[priority] = policy
        for name in sorted({*TOPICS, "vip"}):
            tag = Tag(name=name, created_at=self.ago(800))
            ds.tags.append(tag)
            self.tag_by_name[name] = tag
        for n, noun in enumerate(PRODUCT_NOUNS, start=1):
            price = self.rng.choice(
                [9.99, 14.5, 19.0, 24.99, 39.0, 49.95, 79.0, 129.0, 199.0, 349.0]
            )
            self.catalog.append(
                (f"SKU-{1000 + n}", f"{self.fake.color_name()} {noun}", cents(Decimal(str(price))))
            )
        for i, (title, category, body) in enumerate(ARTICLES, start=1):
            published = i % 5 != 0
            created = self.ago(self.rng.uniform(30, 600))
            ds.articles.append(
                KbArticle(
                    id=f"KB-{i:04d}",
                    title=title,
                    slug=_slug(title).replace(".", "-"),
                    category=category,
                    body=body + " " + self.fake.paragraph(nb_sentences=2),
                    status="published" if published else "draft",
                    author_id=ds.agents[1].id,
                    created_at=created,
                    updated_at=created + timedelta(days=self.rng.uniform(0, 20)),
                    published_at=created if published else None,
                )
            )
        for i, (name, body, actions) in enumerate(MACROS, start=1):
            ds.macros.append(
                Macro(
                    id=f"M-{i:03d}",
                    name=name,
                    description=f"Macro: {name}",
                    body=body,
                    actions=actions,
                    created_at=self.ago(500),
                )
            )
        ds.webhooks.append(
            Webhook(
                id="WH-001",
                url="https://hooks.example.com/helpdesk",
                events=["refund.approved", "ticket.created"],
                secret="whsec_seed_not_a_real_secret",  # noqa: S106
                active=True,
                created_at=self.ago(200),
            )
        )

    def build_customers(self) -> None:
        for i in range(1, N_CUSTOMERS + 1):
            name = self.fake.name()
            created = self.ago(self.rng.uniform(100, 700))
            self.ds.customers.append(
                Customer(
                    id=f"C-{i:04d}",
                    name=name,
                    email=f"{_slug(name)}.{i}@example.com",
                    phone=self.fake.phone_number() if self.rng.random() < 0.6 else None,
                    company=self.fake.company() if self.rng.random() < 0.4 else None,
                    tier=self.weighted({"standard": 80, "plus": 14, "vip": 6}),
                    status="active",
                    created_at=created,
                    updated_at=created,
                )
            )

    # -------------------------------------------------------------------- orders
    def make_order(
        self,
        customer_id: str,
        placed: datetime,
        status: str,
        items: list[tuple[str, str, int, int]] | None = None,
    ) -> Order:
        self.n_order += 1
        if items is None:
            items = []
            for sku, name, price in self.rng.sample(self.catalog, self.rng.randint(1, 4)):
                items.append((sku, name, self.rng.randint(1, 3), price))
        order = Order(
            id=f"O-{self.n_order:04d}",
            customer_id=customer_id,
            status=status,
            currency="USD",
            total_cents=sum(q * p for _, _, q, p in items),
            placed_at=placed,
        )
        order.item_rows = [
            OrderItem(sku=s, name=n, quantity=q, unit_price_cents=p) for s, n, q, p in items
        ]
        if status in ("shipped", "delivered"):
            order.shipped_at = placed + timedelta(hours=self.rng.uniform(6, 48))
        if status == "delivered":
            assert order.shipped_at is not None
            order.delivered_at = order.shipped_at + timedelta(days=self.rng.uniform(1, 5))
        if status == "cancelled":
            order.cancelled_at = placed + timedelta(hours=self.rng.uniform(1, 20))
        self.ds.orders.append(order)
        return order

    def build_orders(self) -> None:
        for _ in range(N_ORDERS):
            customer = self.rng.choice(self.ds.customers)
            placed = self.ago(self.rng.uniform(3, 120))
            status = self.weighted(
                {"delivered": 62, "shipped": 14, "placed": 10, "cancelled": 6, "delivered_late": 8}
            )
            status = "delivered" if status == "delivered_late" else status
            order = self.make_order(customer.id, placed, status)
            if status == "delivered" and order.delivered_at and order.delivered_at > self.as_of:
                order.status = "shipped"
                order.delivered_at = None
        orders_by_status = [o for o in self.ds.orders if o.status == "delivered"]
        for order in orders_by_status:
            assert order.delivered_at is not None
            if self.rng.random() > 0.1 or self.as_of - order.delivered_at < timedelta(days=2):
                continue
            self.make_refund(order, cents_amount=None)

    def make_refund(
        self,
        order: Order,
        cents_amount: int | None,
        status: str | None = None,
        requested_days_after: float | None = None,
    ) -> Refund:
        assert order.delivered_at is not None
        self.n_refund += 1
        amount = cents_amount or (
            order.total_cents
            if self.rng.random() < 0.6
            else max(100, int(order.total_cents * self.rng.uniform(0.2, 0.9)))
        )
        if status is None:
            if amount > 50000:
                status = self.weighted({"pending_approval": 40, "approved": 40, "rejected": 20})
            else:
                status = self.weighted({"approved": 85, "rejected": 15})
        created = order.delivered_at + timedelta(
            days=requested_days_after or self.rng.uniform(0.5, 6)
        )
        created = min(created, self.as_of - timedelta(hours=1))
        lead = self.ds.agents[self.rng.randint(1, 5)]
        refund = Refund(
            id=f"R-{self.n_refund:04d}",
            order_id=order.id,
            customer_id=order.customer_id,
            ticket_id=None,
            amount_cents=amount,
            reason=self.rng.choice(
                ["damaged", "not as described", "late delivery", "changed mind", "wrong item"]
            ),
            status=status,
            requested_by_agent_id=self.ds.agents[self.rng.randint(6, 30)].id,
            created_at=created,
        )
        if status != "pending_approval":
            refund.decided_at = created + timedelta(hours=self.rng.uniform(1, 30))
            refund.decided_by_agent_id = lead.id
            refund.decision_note = (
                "Auto-approved: at or below the approval threshold."
                if status == "approved" and amount <= 50000
                else None
            )
        self.ds.refunds.append(refund)
        return refund

    # ------------------------------------------------------------------- tickets
    def add_comment(
        self,
        ticket: Ticket,
        at: datetime,
        author_type: str,
        author_id: str | None,
        body: str,
        internal: bool = False,
    ) -> Comment:
        self.n_comment += 1
        comment = Comment(
            id=f"CM-{self.n_comment:05d}",
            ticket_id=ticket.id,
            author_type=author_type,
            author_id=author_id,
            body=body,
            is_internal=internal,
            created_at=at,
        )
        self.ds.comments.append(comment)
        return comment

    def make_ticket(
        self,
        customer: Customer,
        created: datetime,
        topic: str | None = None,
        priority: str | None = None,
        status: str | None = None,
    ) -> Ticket:
        rng, ds = self.rng, self.ds
        self.n_ticket += 1
        topic = topic or rng.choice(list(TOPICS))
        subject_tpl, body = rng.choice(TOPICS[topic])
        orders = list(self._orders_of(customer.id))
        order = rng.choice(orders) if orders and topic in ORDER_TOPICS else None
        priority = priority or self.weighted({"low": 25, "normal": 50, "high": 18, "urgent": 7})
        team = ds.teams[TOPIC_TEAM[topic]]
        policy = self.policy_by_priority[priority]
        age_days = (self.as_of - created).total_seconds() / 86400
        ticket = Ticket(
            id=f"T-{self.n_ticket:04d}",
            subject=subject_tpl.format(order=order.id if order else "my order"),
            description=body,
            status="new",
            priority=priority,
            channel=rng.choice(["email", "email", "web", "chat", "phone"]),
            customer_id=customer.id,
            team_id=team.id,
            order_id=order.id if order else None,
            created_at=created,
            updated_at=created,
        )
        ticket.first_response_due_at, ticket.resolution_due_at = compute_due_dates(created, policy)
        ticket.tag_rows = [self.tag_by_name[topic]] + (
            [self.tag_by_name["vip"]] if customer.tier == "vip" else []
        )
        ds.tickets.append(ticket)

        p_active = 0.92 if age_days < 2 else 0.55 if age_days < 14 else 0.12
        active = status in ("new", "open", "pending") if status else rng.random() < p_active
        members = [a for a in ds.agents if a.team_id == team.id and a.active and a.role == "agent"]
        agent = rng.choice(members)
        self.add_comment(ticket, created, "customer", customer.id, body)
        if status == "new" or (status is None and active and rng.random() < 0.2):
            return ticket  # untouched new ticket, nobody has responded
        delay = timedelta(
            minutes=policy.first_response_minutes * rng.choice([0.2, 0.4, 0.6, 0.8, 0.95, 1.3])
        )
        first = created + delay
        if first > self.as_of:
            return ticket
        ticket.assignee_id = agent.id
        ticket.first_response_at = first
        ticket.status = "open"
        self.add_comment(ticket, first, "agent", agent.id, rng.choice(AGENT_LINES))
        t = first
        for _ in range(rng.randint(0, 3)):
            t = min(t + timedelta(hours=rng.uniform(1, 30)), self.as_of)
            kind = rng.random()
            if kind < 0.45:
                self.add_comment(ticket, t, "customer", customer.id, rng.choice(CUSTOMER_LINES))
            elif kind < 0.8:
                self.add_comment(ticket, t, "agent", agent.id, rng.choice(AGENT_LINES))
            else:
                self.add_comment(
                    ticket, t, "agent", agent.id, rng.choice(NOTE_LINES), internal=True
                )
        ticket.updated_at = t
        if active:
            ticket.status = rng.choice(["open", "open", "pending"])
            return ticket
        solved = max(t, first) + timedelta(
            minutes=policy.resolution_minutes * rng.choice([0.2, 0.4, 0.6, 0.8, 0.9, 1.2])
        )
        if solved > self.as_of:
            return ticket
        ticket.solved_at = solved
        ticket.updated_at = solved
        recent = (self.as_of - solved) < timedelta(days=10)
        if recent and rng.random() < 0.6:
            ticket.status = "solved"
        else:
            ticket.status = "closed"
            ticket.closed_at = min(solved + timedelta(days=rng.uniform(1, 3)), self.as_of)
            ticket.updated_at = ticket.closed_at
        if rng.random() < 0.45:
            ticket.csat_score = rng.choices([1, 2, 3, 4, 5], weights=[6, 8, 14, 32, 40])[0]
            ticket.csat_submitted_at = solved + timedelta(hours=rng.uniform(1, 24))
            ticket.csat_submitted_at = min(ticket.csat_submitted_at, self.as_of)
        return ticket

    def _orders_of(self, customer_id: str) -> list[Order]:
        return self._by_customer.get(customer_id, [])

    def build_tickets(self) -> None:
        self._by_customer: dict[str, list[Order]] = {}
        for order in self.ds.orders:
            self._by_customer.setdefault(order.customer_id, []).append(order)
        for _ in range(N_TICKETS):
            customer = self.rng.choice(self.ds.customers)
            created = self.ago(self.rng.uniform(1 / 24, 90))
            self.make_ticket(customer, created)

    # ---------------------------------------------------------------- scenarios
    def build_scenarios(self) -> dict[str, Any]:
        ds = self.ds
        agents = ds.agents
        lead, regular, inactive = agents[1], agents[10], agents[-1]
        sc: dict[str, Any] = {}

        # T-1042: a plain open ticket to close.
        t1042 = next(t for t in ds.tickets if t.id == "T-1042")
        t1042.status, t1042.priority = "open", "normal"
        t1042.assignee_id = t1042.assignee_id or regular.id
        t1042.first_response_at = t1042.first_response_at or t1042.created_at + timedelta(hours=2)
        for attr in ("solved_at", "closed_at", "csat_score", "csat_submitted_at", "merged_into_id"):
            setattr(t1042, attr, None)
        t1042.first_response_due_at, t1042.resolution_due_at = compute_due_dates(
            t1042.created_at, self.policy_by_priority["normal"]
        )
        sc["close_ticket"] = {
            "ticket_id": t1042.id,
            "customer_id": t1042.customer_id,
            "status": "open",
        }

        # T-0217: customer whose most recent order is delivered, in window, small.
        t217 = next(t for t in ds.tickets if t.id == "T-0217")
        o1 = self.make_order(
            t217.customer_id,
            self.ago(2),
            "delivered",
            [("SKU-2001", "Desk Lamp", 2, 4995), ("SKU-2002", "Cable Kit", 1, 3000)],
        )
        o1.shipped_at, o1.delivered_at = self.ago(1.8), self.ago(1)
        self._by_customer.setdefault(t217.customer_id, []).append(o1)
        sc["refund_most_recent_order"] = {
            "ticket_id": t217.id,
            "customer_id": t217.customer_id,
            "order_id": o1.id,
            "order_total": f"{o1.total:.2f}",
            "expected_refund_status": "approved",
        }

        def scenario_order(
            label: str,
            customer_idx: int,
            placed_days: float,
            delivered_days: float | None,
            price_cents: int,
            qty: int = 1,
            status: str = "delivered",
        ) -> Order:
            customer = ds.customers[customer_idx]
            order = self.make_order(
                customer.id,
                self.ago(placed_days),
                status,
                [("SKU-3000", f"{label} item", qty, price_cents)],
            )
            if delivered_days is not None:
                order.shipped_at, order.delivered_at = (
                    self.ago(delivered_days + 0.5),
                    self.ago(delivered_days),
                )
            return order

        pending = scenario_order("Standing desk", 6, 9, 5, 34000, qty=2)
        sc["refund_requires_approval"] = {
            "order_id": pending.id,
            "customer_id": pending.customer_id,
            "order_total": f"{pending.total:.2f}",
            "amount": f"{pending.total:.2f}",
            "expected_refund_status": "pending_approval",
            "approver_agent_id": lead.id,
        }
        old = scenario_order("Monitor arm", 11, 50, 45, 8900)
        sc["refund_outside_window"] = {
            "order_id": old.id,
            "customer_id": old.customer_id,
            "delivered_at": old.delivered_at.isoformat() if old.delivered_at else None,
            "days_since_delivery": 45,
        }
        partial = scenario_order("Keyboard", 16, 14, 10, 20000)
        prior = self.make_refund(
            partial, cents_amount=15000, status="approved", requested_days_after=1
        )
        sc["refund_exceeds_balance"] = {
            "order_id": partial.id,
            "order_total": "200.00",
            "already_refunded": "150.00",
            "refundable": "50.00",
            "prior_refund_id": prior.id,
        }
        waiting = scenario_order("Docking station", 21, 6, 3, 75000)
        wref = self.make_refund(
            waiting, cents_amount=75000, status="pending_approval", requested_days_after=0.5
        )
        sc["pending_refund"] = {
            "refund_id": wref.id,
            "order_id": waiting.id,
            "amount": "750.00",
            "approver_agent_id": lead.id,
        }
        sc["cancellable_order"] = {
            "order_id": scenario_order("Notebook", 26, 0.5, None, 1200, status="placed").id
        }
        sc["shippable_order"] = {
            "order_id": scenario_order("Webcam", 27, 0.4, None, 5900, status="placed").id
        }

        # Ticket scenarios, appended so they do not disturb the random population.
        def fresh(customer_idx: int, hours_ago: float, **kw: Any) -> Ticket:
            return self.make_ticket(
                ds.customers[customer_idx], self.as_of - timedelta(hours=hours_ago), **kw
            )

        reopen_ok = fresh(30, 80, topic="login", priority="normal", status="open")
        self._force_solved(reopen_ok, days_ago=2, regular=regular)
        reopen_late = fresh(31, 400, topic="bug", priority="normal", status="open")
        self._force_solved(reopen_late, days_ago=12, regular=regular)
        sc["reopen_within_window"] = {"ticket_id": reopen_ok.id, "solved_days_ago": 2}
        sc["reopen_after_window"] = {"ticket_id": reopen_late.id, "solved_days_ago": 12}
        m_target = self._ensure_open(
            fresh(32, 20, topic="billing", priority="normal", status="open"), regular
        )
        m_source = self._ensure_open(
            fresh(32, 18, topic="billing", priority="normal", status="open"), regular
        )
        sc["merge_tickets"] = {
            "source_ticket_id": m_source.id,
            "target_ticket_id": m_target.id,
            "customer_id": m_target.customer_id,
        }
        dup_a = ds.customers[498]
        dup_b = ds.customers[499]
        dup_b.name = dup_a.name
        t_a = self._ensure_open(
            self.make_ticket(dup_a, self.ago(0.3), topic="how-to", status="open"), regular
        )
        t_b = self._ensure_open(
            self.make_ticket(dup_b, self.ago(0.2), topic="how-to", status="open"), regular
        )
        sc["merge_customers"] = {
            "source_customer_id": dup_b.id,
            "target_customer_id": dup_a.id,
            "source_ticket_id": t_b.id,
            "target_ticket_id": t_a.id,
        }
        esc = self._ensure_open(
            fresh(33, 10, topic="outage", priority="normal", status="open"), regular
        )
        sc["escalate_ticket"] = {
            "ticket_id": esc.id,
            "priority": "normal",
            "expected_priority_after": "high",
        }
        unassigned = fresh(34, 3, topic="how-to", status="new")
        sc["unassigned_new_ticket"] = {"ticket_id": unassigned.id}
        kb = next(a for a in ds.articles if a.title == "How to reset your password")
        sc["kb_lookup"] = {
            "query": "reset password",
            "expected_article_id": kb.id,
            "status": kb.status,
        }
        sc["agents"] = {
            "admin": agents[0].id,
            "lead": lead.id,
            "regular": regular.id,
            "inactive": inactive.id,
            "team_ids": {t.name: t.id for t in ds.teams},
        }
        return sc

    def _ensure_open(self, ticket: Ticket, agent: Agent) -> Ticket:
        """Scenario tickets must be open and assigned even if the random flow left them new."""
        if ticket.status == "new":
            ticket.status = "open"
            ticket.assignee_id = agent.id
            ticket.first_response_at = ticket.created_at + timedelta(minutes=30)
        return ticket

    def _force_solved(self, ticket: Ticket, days_ago: float, regular: Agent) -> None:
        ticket.assignee_id = ticket.assignee_id or regular.id
        ticket.status = "solved"
        ticket.solved_at = self.ago(days_ago)
        ticket.updated_at = ticket.solved_at
        ticket.closed_at = None
        ticket.first_response_at = ticket.first_response_at or ticket.created_at + timedelta(
            hours=1
        )

    # ------------------------------------------------------------------ facts
    def compute_facts(self) -> dict[str, Any]:
        ds = self.ds
        by_status: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        workload: dict[str, int] = {}
        per_customer: dict[str, int] = {}
        breached: list[str] = []
        for t in ds.tickets:
            by_status[t.status] = by_status.get(t.status, 0) + 1
            by_priority[t.priority] = by_priority.get(t.priority, 0) + 1
            per_customer[t.customer_id] = per_customer.get(t.customer_id, 0) + 1
            if t.status in ("new", "open", "pending") and t.assignee_id:
                workload[t.assignee_id] = workload.get(t.assignee_id, 0) + 1
            if evaluate_ticket(t, self.policy_by_priority[t.priority], self.as_of).breached:
                breached.append(t.id)
        top_agent = sorted(workload.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        top_customer = sorted(per_customer.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        return {
            "counts": {
                "customers": len(ds.customers),
                "agents": len(ds.agents),
                "teams": len(ds.teams),
                "tickets": len(ds.tickets),
                "comments": len(ds.comments),
                "orders": len(ds.orders),
                "refunds": len(ds.refunds),
                "kb_articles": len(ds.articles),
                "macros": len(ds.macros),
                "tags": len(ds.tags),
            },
            "tickets_by_status": dict(sorted(by_status.items())),
            "tickets_by_priority": dict(sorted(by_priority.items())),
            "sla_breached_ticket_count": len(breached),
            "busiest_agent": {"agent_id": top_agent[0], "active_tickets": top_agent[1]},
            "customer_with_most_tickets": {
                "customer_id": top_customer[0],
                "tickets": top_customer[1],
            },
            "refunds_pending_approval": sorted(
                r.id for r in ds.refunds if r.status == "pending_approval"
            ),
        }


def build_dataset(seed: int = DEFAULT_SEED, as_of: datetime = DEFAULT_AS_OF) -> Dataset:
    b = _Builder(seed, as_of)
    b.build_reference()
    b.build_customers()
    b.build_orders()
    b.build_tickets()
    scenarios = b.build_scenarios()
    b.ds.fixtures = {
        "seed": seed,
        "as_of": as_of.isoformat(),
        "note": "Generated by `helpdesk seed`. Run the API with HELPDESK_FROZEN_TIME=<as_of> so "
        "refund and reopen windows match this data.",
        **b.compute_facts(),
        "scenarios": scenarios,
    }
    return b.ds


async def clear_database(session: AsyncSession) -> None:
    for table in reversed(Base.metadata.sorted_tables):
        await session.execute(delete(table))
    await session.commit()


async def seed_database(
    session: AsyncSession, seed: int = DEFAULT_SEED, as_of: datetime = DEFAULT_AS_OF
) -> dict[str, Any]:
    """Write the dataset. The database must be empty (see ``clear_database``)."""
    ds = build_dataset(seed, as_of)
    # Flush in dependency order: the ORM does not infer cross-table order without relationships.
    for batch in (
        [*ds.teams, *ds.policies, *ds.tags],
        ds.agents,
        ds.customers,
        [*ds.orders, *ds.articles, *ds.macros, *ds.webhooks],
        ds.tickets,
        [*ds.comments, *ds.refunds],
    ):
        session.add_all(batch)
        await session.flush()
    counters = {
        "customer": len(ds.customers),
        "agent": len(ds.agents),
        "team": len(ds.teams),
        "ticket": len(ds.tickets),
        "comment": len(ds.comments),
        "order": len(ds.orders),
        "refund": len(ds.refunds),
        "sla": len(ds.policies),
        "macro": len(ds.macros),
        "kb": len(ds.articles),
        "webhook": len(ds.webhooks),
        "api_key": 0,
    }
    for name, value in counters.items():
        session.add(Sequence(name=name, value=value))
    await session.commit()
    return ds.fixtures


async def is_empty(session: AsyncSession) -> bool:
    return (await session.scalar(select(Customer.id).limit(1))) is None


async def fingerprint(session: AsyncSession) -> str:
    """SHA-256 over every row of every table, in primary-key order. Equal means identical data."""
    digest = hashlib.sha256()
    for table in Base.metadata.sorted_tables:
        pk = ", ".join(c.name for c in table.primary_key.columns)
        rows = (await session.execute(text(f"SELECT * FROM {table.name} ORDER BY {pk}"))).all()  # noqa: S608
        digest.update(table.name.encode())
        for row in rows:
            digest.update(json.dumps([str(v) for v in row]).encode())
    return digest.hexdigest()
