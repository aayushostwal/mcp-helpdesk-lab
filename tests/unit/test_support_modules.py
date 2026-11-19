"""Small modules: security, cursors, config, KB helpers, webhooks, audit helpers, clock, logging."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel

from helpdesk.api.schemas.common import Money
from helpdesk.clock import FrozenClock, SystemClock, clock_from_settings
from helpdesk.config import Settings, normalize_database_url
from helpdesk.errors import InvalidCursorError, ValidationFailedError
from helpdesk.integrations.webhook_sender import (
    DeliveryResult,
    HttpxWebhookSender,
    SimulatedWebhookSender,
)
from helpdesk.logging import configure_logging, get_logger
from helpdesk.metrics import Metrics
from helpdesk.models import Customer, KbArticle
from helpdesk.repositories.base import decode_cursor, encode_cursor, like_pattern
from helpdesk.security import (
    SCOPES,
    generate_key,
    hash_secret,
    scopes_allow,
    split_token,
    verify_secret,
)
from helpdesk.services.base import diff, jsonable, new_event_id, snapshot
from helpdesk.services.knowledge import rank, slugify
from helpdesk.services.macros import render_macro
from helpdesk.services.webhooks import assert_target_allowed, sign_payload

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


# -------------------------------------------------------------------------- security


def test_generated_keys_are_unique_and_parseable() -> None:
    a, b = generate_key(), generate_key()
    assert a.token != b.token and a.salt != b.salt
    prefix, secret = split_token(a.token) or ("", "")
    assert prefix == a.prefix
    assert verify_secret(a.salt, secret, a.key_hash)
    assert not verify_secret(a.salt, secret + "x", a.key_hash)
    assert not verify_secret(b.salt, secret, a.key_hash)
    assert secret not in a.key_hash


def test_hash_depends_on_salt() -> None:
    assert hash_secret("s1", "x") != hash_secret("s2", "x")
    assert len(hash_secret("s", "x")) == 64


@pytest.mark.parametrize("token", ["", "hd_", "hd_abc", "hd__x", "hd_abc_", "xx_abc_def", "Bearer"])
def test_split_token_rejects_malformed(token: str) -> None:
    assert split_token(token) is None


def test_scope_matching() -> None:
    assert scopes_allow(frozenset({"admin"}), "tickets:write")
    assert scopes_allow(frozenset({"tickets:write"}), "tickets:write")
    assert not scopes_allow(frozenset({"tickets:read"}), "tickets:write")
    assert "admin" in SCOPES and len(SCOPES) == len(set(SCOPES))


# ---------------------------------------------------------------------------- cursors


def test_cursor_round_trip() -> None:
    for value in (NOW, 42, "T-0001"):
        decoded, row_id = decode_cursor(encode_cursor(value, "X-1"))
        assert decoded == value and row_id == "X-1"


@pytest.mark.parametrize("cursor", ["", "!!!", "e30", "WyJ4IiwgMSwgMl0", "bm90LWpzb24"])
def test_bad_cursors_raise(cursor: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor)


def test_like_pattern_escapes_wildcards() -> None:
    assert like_pattern("50%_off\\") == "%50\\%\\_off\\\\%"


# ---------------------------------------------------------------------------- config


def test_database_url_normalisation() -> None:
    assert normalize_database_url("sqlite:///x.db") == "sqlite+aiosqlite:///x.db"
    assert normalize_database_url("postgres://u@h/db") == "postgresql+asyncpg://u@h/db"
    assert normalize_database_url("postgresql://u@h/db") == "postgresql+asyncpg://u@h/db"
    assert normalize_database_url("postgresql+asyncpg://u@h/db") == "postgresql+asyncpg://u@h/db"
    assert normalize_database_url("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/helpdesk")
    monkeypatch.setenv("HELPDESK_REFUND_APPROVAL_THRESHOLD", "250.50")
    monkeypatch.setenv("HELPDESK_FROZEN_TIME", "2026-06-01T12:00:00+00:00")
    s = Settings()
    assert s.database_url == "postgresql+asyncpg://u:p@db/helpdesk" and not s.is_sqlite
    assert s.refund_approval_threshold == Decimal("250.50")
    assert s.frozen_time == NOW


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.is_sqlite and s.refund_window_days == 30 and s.reopen_window_days == 7
    assert s.refund_approval_threshold == Decimal("500.00") and s.idempotency_ttl_hours == 24


# ----------------------------------------------------------------------------- clock


def test_clocks() -> None:
    frozen = FrozenClock(datetime(2026, 1, 1))  # naive input is treated as UTC
    assert frozen.now() == datetime(2026, 1, 1, tzinfo=UTC)
    frozen.set(datetime(2026, 2, 1))
    assert frozen.now().month == 2
    assert abs(SystemClock().now() - datetime.now(UTC)) < timedelta(seconds=5)
    assert isinstance(clock_from_settings(None), SystemClock)
    assert isinstance(clock_from_settings(NOW), FrozenClock)


# ------------------------------------------------------------------------------- KB


def test_slugify() -> None:
    assert slugify("How to Reset Your Password!") == "how-to-reset-your-password"
    assert slugify("!!!") == "article"
    assert len(slugify("a" * 500)) == 120


def test_rank_prefers_title_hits() -> None:
    a = KbArticle(title="Password reset", body="nothing here", id="1")
    b = KbArticle(title="Other", body="password reset password reset", id="2")
    assert rank(a, ["password", "reset"])[0] == 6
    assert rank(b, ["password", "reset"])[0] == 4
    score, snippet = rank(
        KbArticle(title="x", body="Use the reset link\nto continue", id="3"), ["reset"]
    )
    assert score == 1 and "\n" not in snippet and "reset link" in snippet
    assert rank(KbArticle(title="x", body="short", id="4"), ["zzz"])[0] == 0


def test_render_macro() -> None:
    assert (
        render_macro("Hi {{name}} {{name}} {{missing}}", {"name": "Al"}) == "Hi Al Al {{missing}}"
    )


# ------------------------------------------------------------------------- webhooks


def test_signature_is_stable_and_secret_dependent() -> None:
    assert sign_payload("s", b"body") == sign_payload("s", b"body")
    assert sign_payload("s", b"body") != sign_payload("t", b"body")
    assert sign_payload("s", b"body").startswith("sha256=")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://192.168.1.5/",
        "http://10.1.1.1/",
        "http://169.254.169.254/",
        "http://svc.internal/",
        "http://nas.local/",
        "http:///nohost",
    ],
)
def test_private_targets_are_refused(url: str) -> None:
    with pytest.raises(ValidationFailedError):
        assert_target_allowed(url, allow_private=False)


def test_private_targets_can_be_allowed_and_public_pass() -> None:
    assert_target_allowed("http://localhost/x", allow_private=True)
    assert_target_allowed("https://example.com/x", allow_private=False)
    assert_target_allowed("https://93.184.216.34/x", allow_private=False)


async def test_simulated_sender_never_fails() -> None:
    result = await SimulatedWebhookSender().send("https://example.com", {}, b"{}", 1.0)
    assert result == DeliveryResult(True, 200, None, 0)


async def test_httpx_sender_reports_transport_errors() -> None:
    result = await HttpxWebhookSender().send("http://127.0.0.1:9/unreachable", {}, b"{}", 1.0)
    assert result.success is False and result.status_code is None and result.error


# -------------------------------------------------------------------- audit helpers


def test_snapshot_hides_secrets_and_serialises() -> None:
    from helpdesk.models import ApiKey

    key = ApiKey(
        id="K-1", name="n", prefix="p", salt="SALT", key_hash="HASH", scopes=["a"], created_at=NOW
    )
    snap = snapshot(key, {"extra": 1})
    assert "salt" not in snap and "key_hash" not in snap and snap["extra"] == 1
    assert snap["created_at"] == NOW.isoformat()
    json.dumps(snap)  # must be JSON-safe
    c = Customer(id="C-1", name="n", email="e", created_at=NOW, updated_at=NOW)
    assert snapshot(c)["email"] == "e"


def test_diff_keeps_only_changes() -> None:
    before, after = diff({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})
    assert before == {"b": 2, "c": None} and after == {"b": 3, "c": 4}
    assert diff(None, {"a": 1}) == (None, {"a": 1})
    assert diff({"a": 1}, None) == ({"a": 1}, None)


def test_jsonable_and_event_ids_are_ordered() -> None:
    assert (
        jsonable(Decimal("1.50")) == "1.50"
        and jsonable(NOW) == NOW.isoformat()
        and jsonable(3) == 3
    )
    ids = [new_event_id() for _ in range(50)]
    assert ids == sorted(ids) and len(set(ids)) == 50


# --------------------------------------------------------------------- schemas, logs


def test_money_serialises_as_two_decimal_string() -> None:
    class M(BaseModel):
        amount: Money

    assert M(amount=Decimal("5")).model_dump_json() == '{"amount":"5.00"}'
    assert M(amount="12.5").model_dump(mode="json") == {"amount": "12.50"}


def test_metrics_render() -> None:
    m = Metrics()
    m.observe("GET", "/x/{id}", 200, 0.01)
    text = m.render().decode()
    assert 'helpdesk_http_requests_total{method="GET",route="/x/{id}",status="200"} 1.0' in text


def test_logging_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json_logs=True)
    get_logger().info("hello", answer=42)
    record = json.loads(capsys.readouterr().out.strip())
    assert record["event"] == "hello" and record["answer"] == 42 and record["level"] == "info"
    configure_logging("WARNING", json_logs=False)
    get_logger().info("quiet")
    assert capsys.readouterr().out == ""
