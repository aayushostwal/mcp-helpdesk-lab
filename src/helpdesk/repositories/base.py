"""Shared repository helpers: id sequences and keyset (cursor) pagination."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence as Seq
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, insert, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from helpdesk.errors import InvalidCursorError
from helpdesk.models import Sequence


async def next_id(session: AsyncSession, name: str, prefix: str, width: int = 4) -> str:
    """Allocate the next human-readable id, e.g. ``T-1043``.

    The ``UPDATE ... RETURNING`` takes a row lock on PostgreSQL, so concurrent transactions
    serialise on the counter and never hand out the same id.
    """
    stmt = (
        update(Sequence)
        .where(Sequence.name == name)
        .values(value=Sequence.value + 1)
        .returning(Sequence.value)
    )
    value = (await session.execute(stmt)).scalar_one_or_none()
    if value is None:
        await session.execute(insert(Sequence).values(name=name, value=1))
        value = 1
    return f"{prefix}-{value:0{width}d}"


async def set_sequence(session: AsyncSession, name: str, value: int) -> None:
    existing = await session.get(Sequence, name)
    if existing is None:
        session.add(Sequence(name=name, value=value))
    else:
        existing.value = value


def encode_cursor(sort_value: Any, row_id: Any) -> str:
    if isinstance(sort_value, datetime):
        payload = ["dt", sort_value.isoformat(), row_id]
    elif isinstance(sort_value, int):
        payload = ["i", sort_value, row_id]
    else:
        payload = ["s", str(sort_value), row_id]
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[Any, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        kind, value, row_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if kind == "dt":
            return datetime.fromisoformat(value), row_id
        if kind in ("i", "s"):
            return value, row_id
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("The pagination cursor is malformed.") from exc
    raise InvalidCursorError("The pagination cursor is malformed.")


@dataclass
class Page[T]:
    items: Seq[T]
    next_cursor: str | None


async def paginate[T](
    session: AsyncSession,
    stmt: Select[T],
    *,
    sort: InstrumentedAttribute[Any],
    id_col: InstrumentedAttribute[Any],
    limit: int,
    cursor: str | None,
    descending: bool = True,
) -> Page[T]:
    """Keyset pagination over ``(sort, id)``. Stable under concurrent inserts."""
    if cursor:
        value, row_id = decode_cursor(cursor)
        if descending:
            cond = or_(sort < value, and_(sort == value, id_col < row_id))
        else:
            cond = or_(sort > value, and_(sort == value, id_col > row_id))
        stmt = stmt.where(cond)
    order = (sort.desc(), id_col.desc()) if descending else (sort.asc(), id_col.asc())
    rows = list((await session.scalars(stmt.order_by(*order).limit(limit + 1))).all())
    if len(rows) <= limit:
        return Page(items=rows, next_cursor=None)
    items = rows[:limit]
    last = items[-1]
    return Page(
        items=items,
        next_cursor=encode_cursor(getattr(last, sort.key), getattr(last, id_col.key)),
    )


def like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
