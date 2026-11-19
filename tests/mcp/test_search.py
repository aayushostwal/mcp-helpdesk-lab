"""BM25 unit tests and the ranking test (queries a person might type, expected op in top 3)."""

from __future__ import annotations

import pytest

from helpdesk_mcp.search import Bm25Index, build_index, stem, tokenize
from helpdesk_mcp.spec import Spec

# (query, expected operation id). Written from the point of view of someone who has not read the
# operation ids. All expected operations must appear in the top 3.
RANKING_CASES: list[tuple[str, str]] = [
    ("close a ticket", "tickets_close"),
    ("reopen a solved ticket", "tickets_reopen"),
    ("refund an order", "refunds_create"),
    ("approve a pending refund", "refunds_approve"),
    ("reject a refund request", "refunds_reject"),
    ("send a reply to the customer", "comments_add_reply"),
    ("leave an internal note on a ticket", "comments_add_note"),
    ("escalate a ticket", "tickets_escalate"),
    ("assign ticket to an agent", "tickets_assign"),
    ("merge duplicate customers", "customers_merge"),
    ("merge two tickets", "tickets_merge"),
    ("cancel an order", "orders_cancel"),
    ("mark order as shipped", "orders_ship"),
    ("search help center articles", "kb_articles_search"),
    ("which tickets are breaching their SLA", "sla_list_breaches"),
    ("what is the workload of each agent", "reports_agent_workload"),
    ("customer satisfaction scores", "reports_csat_summary"),
    ("who is on call for a team", "teams_get_oncall"),
    ("add a tag to a ticket", "tickets_add_tag"),
    ("list orders of a customer", "customers_list_orders"),
    ("create a webhook", "webhooks_create"),
    ("revoke an api key", "admin_api_keys_revoke"),
    ("create a webhook", "webhooks_create"),
    ("apply a canned response macro", "macros_apply"),
    ("deactivate an agent", "agents_deactivate"),
]


# Known lexical gap, kept out of the pass/fail list on purpose: BM25 only matches words, so a
# query that uses none of the operation's own vocabulary misses. See docs/mcp-server.md.
KNOWN_MISSES: list[tuple[str, str]] = [("how busy is each agent", "reports_agent_workload")]


@pytest.fixture(scope="module")
def index(spec: Spec) -> Bm25Index:
    return build_index(spec.operations.values())


def test_ranking_hit_rate(index: Bm25Index, capsys: pytest.CaptureFixture[str]) -> None:
    misses = []
    for query, expected in RANKING_CASES:
        top = [hit.operation_id for hit in index.search(query, 3)]
        if expected not in top:
            misses.append((query, expected, top))
    hits = len(RANKING_CASES) - len(misses)
    with capsys.disabled():
        print(f"\nBM25 top-3 hit rate: {hits}/{len(RANKING_CASES)}")
    assert not misses, misses


def test_known_vocabulary_gap_is_real(index: Bm25Index) -> None:
    """If this starts passing, move the query into RANKING_CASES."""
    for query, expected in KNOWN_MISSES:
        assert expected not in [hit.operation_id for hit in index.search(query, 3)]


def test_ranking_top_one_for_clear_queries(index: Bm25Index) -> None:
    for query, expected in [
        ("close a ticket", "tickets_close"),
        ("escalate a ticket", "tickets_escalate"),
        ("merge duplicate customers", "customers_merge"),
        ("create a webhook", "webhooks_create"),
    ]:
        assert index.search(query, 1)[0].operation_id == expected


def test_case_snake_case_and_plural_forms_match(index: Bm25Index) -> None:
    a = [h.operation_id for h in index.search("TICKETS_CLOSE", 3)]
    b = [h.operation_id for h in index.search("closing tickets", 3)]
    assert a[0] == "tickets_close"
    assert "tickets_close" in b


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("tickets", "ticket"),
        ("closed", "clos"),
        ("closing", "clos"),
        ("close", "clos"),
        ("cancelled", "cancel"),
        ("shipping", "ship"),
        ("policies", "policy"),
        ("addresses", "address"),
        ("status", "status"),
        ("boss", "boss"),
        ("api", "api"),
        ("404", "404"),
    ],
)
def test_stem(word: str, expected: str) -> None:
    assert stem(word) == expected


def test_tokenize_splits_and_drops_stop_words() -> None:
    assert tokenize("Close the Ticket_by customerId") == ["clos", "ticket", "customer", "id"]
    assert tokenize("a the of") == []


def test_unknown_or_empty_queries_return_nothing(index: Bm25Index) -> None:
    assert index.search("zzzzzz qqqq", 5) == []
    assert index.search("", 5) == []
    assert index.search("ticket", 0) == []


def test_limit_and_order_are_stable(index: Bm25Index) -> None:
    hits = index.search("ticket", 7)
    assert len(hits) == 7
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert hits == index.search("ticket", 7)


def test_bm25_prefers_rarer_terms_and_shorter_documents() -> None:
    index = Bm25Index(
        [("a", "apple banana"), ("b", "apple banana cherry cherry cherry"), ("c", "banana")]
    )
    assert index.search("apple", 3)[0].operation_id == "a"  # shorter doc wins on equal tf
    assert index.search("cherry banana", 1)[0].operation_id == "b"  # rare term dominates
