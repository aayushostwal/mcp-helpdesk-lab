"""Typed domain errors. Services raise these; one handler maps them to RFC 9457 responses."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class. ``slug`` becomes the stable tail of the problem ``type`` URI."""

    status_code: int = 400
    slug: str = "error"
    title: str = "Error"

    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class NotFoundError(DomainError):
    status_code = 404
    slug = "not-found"
    title = "Resource not found"

    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} '{entity_id}' was not found.", entity=entity, id=entity_id)


class ConflictError(DomainError):
    status_code = 409
    slug = "conflict"
    title = "Conflict with current state"


class InvalidTransitionError(ConflictError):
    slug = "invalid-status-transition"
    title = "Invalid status transition"


class ReopenWindowExpiredError(ConflictError):
    slug = "reopen-window-expired"
    title = "Reopen window expired"


class ValidationFailedError(DomainError):
    status_code = 422
    slug = "validation-failed"
    title = "Request is not valid"


class PolicyViolationError(DomainError):
    """A well-formed request that a business rule forbids."""

    status_code = 422
    slug = "policy-violation"
    title = "Business rule violated"


class RefundNotEligibleError(PolicyViolationError):
    slug = "refund-not-eligible"
    title = "Order is not eligible for a refund"


class RefundExceedsBalanceError(PolicyViolationError):
    slug = "refund-exceeds-balance"
    title = "Refund exceeds refundable balance"


class UnauthorizedError(DomainError):
    status_code = 401
    slug = "unauthorized"
    title = "Authentication required"


class ForbiddenError(DomainError):
    status_code = 403
    slug = "forbidden"
    title = "Forbidden"


class InsufficientScopeError(ForbiddenError):
    slug = "insufficient-scope"
    title = "Insufficient scope"


class AgentRoleRequiredError(ForbiddenError):
    slug = "agent-role-required"
    title = "Agent role required"


class IdempotencyKeyReusedError(DomainError):
    status_code = 422
    slug = "idempotency-key-reuse"
    title = "Idempotency key reused with a different request"


class InvalidCursorError(DomainError):
    status_code = 400
    slug = "invalid-cursor"
    title = "Invalid pagination cursor"
