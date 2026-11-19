"""The text of the three context features: server instructions, resources and prompts.

Everything here is written to match what the API actually enforces (see docs/api.md and the
rules in ``helpdesk.services``). If a rule changes in the API, change it here too. The prompts and
resources use operation ids (for example ``refunds_create``) because those names exist in every
tool variant: as tool names, as ``action`` values' targets, or as search results.
"""

from __future__ import annotations

INSTRUCTIONS = """\
Helpdesk for an online shop. Writes change real data.

How it fits together
- A customer (C-0311) has tickets (T-1042) and orders (O-1502). An order has line items and can \
have refunds (R-0111). Agents (A-0002) work tickets and belong to teams (TM-001).
- IDs are a letter prefix plus a zero-padded number: T-1042, C-0311, O-1502, R-0111, A-0002. \
Use them exactly as returned.
- Search before you write. Find the customer, ticket or order with the list or search \
operations, then read it before changing it. Lists are paged: pass next_cursor back as cursor.

Rules enforced by the API
- Ticket status goes new, open, pending, solved, closed. A closed ticket cannot be changed. A \
solved ticket can be reopened for 7 days. Use a public reply to write to the customer and an \
internal note for staff-only remarks.
- A refund needs an order ID, an amount and a reason. The order must be delivered or shipped, \
the request must be within 30 days of delivery, and the amount cannot exceed what is still \
refundable. Refunds over 500.00 wait for a lead's approval; do not promise them.
- Money is a string with two decimals, such as "129.90".
- A failed call returns isError with a problem document. Read its type and detail. Do not \
repeat the same call unchanged.

Resources: helpdesk://schema, helpdesk://policies/refunds, helpdesk://policies/sla, \
helpdesk://tickets/{ticket_id}. Prompts: triage_ticket, weekly_sla_report, refund_request.
"""

SCHEMA_RESOURCE = """\
# Helpdesk data model

## Entities and ids

| Entity | Id format | Belongs to |
|---|---|---|
| Customer | `C-0311` | |
| Ticket | `T-1042` | one customer; optionally one order, one assignee, one team |
| Comment | `CM-00123` | one ticket; public reply or internal note |
| Order | `O-1502` | one customer; has line items |
| Refund | `R-0111` | one order (and its customer); optionally one ticket |
| Agent (staff) | `A-0002` | optionally one team; role `agent`, `lead` or `admin` |
| Team | `TM-001` | has agents; one on-call agent |
| Knowledge base article | `KB-0001` | optionally an author agent |
| Macro | `M-001` | canned reply with optional actions |
| SLA policy | `SLA-001` | one per priority |
| Webhook | `WH-001` | |
| API key | `K-003` | optionally linked to an agent |

Ids are strings. The number is zero-padded (four digits for most, three for teams, policies,
webhooks, keys and macros, five for comments). Use them exactly as returned.

## Relationships

- A customer has many tickets, orders and refunds. A ticket never belongs to two customers.
- A ticket may mention one order, and that order must belong to the ticket's customer.
- A refund belongs to an order. The sum of a customer's refunds on one order cannot exceed the
  order total.
- Tickets have tags (many to many) and comments (oldest first).

## Value sets

| Field | Values |
|---|---|
| Ticket status | `new`, `open`, `pending`, `solved`, `closed` |
| Ticket priority | `low`, `normal`, `high`, `urgent` |
| Ticket channel | `email`, `web`, `chat`, `phone` |
| Customer tier | `standard`, `plus`, `vip` |
| Customer status | `active`, `merged` (a merged customer points at the survivor) |
| Order status | `placed`, `shipped`, `delivered`, `cancelled` |
| Refund status | `pending_approval`, `approved`, `rejected`, `cancelled` |
| Article status | `draft`, `published` |

## Conventions

- Money is a string with two decimals (`"129.90"`), currency on the order.
- Timestamps are ISO 8601 in UTC.
- List operations return `{"items": [...], "next_cursor": "..."}` and take `limit` (1 to 100)
  and `cursor`. Short reference lists (teams, tags, SLA policies, webhooks, API keys) return a
  plain array.
- Errors are RFC 9457 problem documents. Match on `type`, not on `detail`.
- Order lifecycle: `placed` -> `shipped` -> `delivered`; only `placed` orders can be cancelled.
- Merging tickets or customers moves the data to the target and marks the source; repeating the
  same merge changes nothing.
"""

REFUND_POLICY_RESOURCE = """\
# Refund policy

These are the rules the API applies in `refunds_create`. The numbers are the defaults; an admin
can change them at runtime (`admin_settings_get` shows the current values).

1. **Order status.** The order must be `delivered` or `shipped`. Orders that are `placed` or
   `cancelled` are not refundable (422, `refund-not-eligible`).
2. **Window.** The request must come within **30 days** of delivery. A shipped order with no
   delivery date uses its ship date. Outside the window: 422, `refund-not-eligible`.
3. **Amount.** The amount must be at most the order total minus refunds on that order that are
   `approved` or `pending_approval`. Rejected and cancelled refunds release their amount. Too
   much: 422, `refund-exceeds-balance`, and the problem lists the `refundable` amount.
4. **Approval.** Up to and including **500.00** the refund is `approved` at once. Above 500.00 it
   is created as `pending_approval`. Tell the customer it is waiting for review; do not say it
   is approved.
5. **Who decides.** Only an API key linked to an agent with role `lead` can run
   `refunds_approve` or `refunds_reject` (403, `agent-role-required` otherwise). Any key with
   refund write access can run `refunds_cancel` on a pending refund.

Every refund needs an `order_id`, an `amount` and a `reason`; `ticket_id` is optional but useful.
Refund creation is protected against double submission: a retry of the same call is not
processed twice.

Typical flow: read the order (`orders_get`), check earlier refunds (`orders_list_refunds`),
then `refunds_create`. If the answer is a policy error, explain it; do not try to work around it.
"""

SLA_POLICY_RESOURCE = """\
# SLA policy

Each priority has a policy with two targets, in minutes. The default policies are:

| Priority | First response | Resolution |
|---|---|---|
| urgent | 30 | 240 |
| high | 60 | 480 |
| normal | 240 | 1440 |
| low | 480 | 4320 |

`sla_policies_list` shows the values in force; they can be edited.

## How a target is judged

- Due times are stored on the ticket when it is created or its priority changes: created time
  plus the policy minutes for the current priority.
- A **first response** is the first public reply by staff. Internal notes and customer messages
  do not count.
- **Resolution** is the time the ticket was solved.
- Each target is `met` (achieved by its due time), `breached` (achieved late, or still open past
  due), `at_risk` (still open with less than 20% of its window left) or `pending`.
- A ticket is breached if either target is breached. `tickets_list` can filter on
  `sla_breached`, `sla_list_breaches` lists them, `sla_evaluate_ticket` explains one ticket.

## Escalation

`tickets_escalate` raises the priority one step (up to `urgent`), increases the escalation level
(maximum 3) and recomputes the due times from the ticket's creation time using the new priority.
Solved and closed tickets cannot be escalated.
"""


def triage_ticket_prompt(ticket_id: str) -> str:
    return f"""\
Triage ticket {ticket_id}.

1. Read the ticket (`tickets_get`) and its conversation (`comments_list`, include internal notes).
2. Look at the customer (`customers_get_summary`) and, if the ticket names an order, the order
   (`orders_get`).
3. Check the SLA (`sla_evaluate_ticket`). Read helpdesk://policies/sla if you need the rules.
4. Decide: the right priority, the team that should own it (`teams_list`), whether a knowledge
   base article answers it (`kb_articles_search`), and what the next action is.
5. Apply the routing you decided on with `tickets_update` or `tickets_assign`, and add useful
   tags with `tickets_add_tag`. Do not close the ticket, send a public reply or create a refund
   during triage.
6. Finish with a short summary: what the problem is, what you changed, what should happen next.
"""


def weekly_sla_report_prompt(team: str | None = None) -> str:
    scope = f"for team {team}" if team else "for all teams"
    team_step = (
        f"Find the id of team {team} with `teams_list` (it is `TM-` followed by a number) and "
        "use `team_id` filters where an operation has one."
        if team
        else "Use the report operations without a team filter."
    )
    return f"""\
Write the weekly SLA report {scope}, covering the last 7 days.

1. Work out the period. If you do not know today's date, use the newest ticket's creation date
   (`tickets_list`) as the end. Use ISO 8601 date-times for `start` and `end`.
2. {team_step}
3. Get the numbers: `reports_sla_compliance` for the period, `sla_list_breaches` for tickets
   currently breaching, and `reports_agent_workload` for load.
4. Name the three oldest or highest-priority breaching tickets with their ids and owners.
5. Write the report: headline compliance rate, what got worse or better, the breaching tickets
   to act on first, and one recommendation. Use plain language and cite ticket ids.
6. Read-only task: do not change any ticket.
"""


def refund_request_prompt(order_id: str, reason: str) -> str:
    return f"""\
A customer asks for a refund on order {order_id}. Their reason: "{reason}".

1. Read the order (`orders_get`) and earlier refunds on it (`orders_list_refunds`). Read
   helpdesk://policies/refunds for the rules.
2. Decide whether a refund is allowed and for how much: order status, the 30-day window,
   and what is still refundable. By default, refund the full refundable amount only when the
   reason supports it; otherwise ask.
3. If it is allowed, create it with `refunds_create` (`order_id`, `amount` as a two-decimal
   string, `reason`, and `ticket_id` if you know it). If it is not, do not create anything.
4. Report the outcome in plain words: the refund id and status, or the rule that blocks it.
   If the status is `pending_approval`, say that a lead must approve it. Do not approve it
   yourself.
"""
