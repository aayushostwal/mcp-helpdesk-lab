from __future__ import annotations

import itertools

import pytest

from helpdesk_mcp.spec import Spec
from helpdesk_mcp.subset import ALWAYS_INCLUDED, ToolCountError, select_operations


def test_always_included_names_exist(spec: Spec) -> None:
    assert set(ALWAYS_INCLUDED) <= set(spec.operations)
    assert len(set(ALWAYS_INCLUDED)) == len(ALWAYS_INCLUDED)


@pytest.mark.parametrize("count", [10, 25, 50, 100])
def test_subset_size_and_determinism(spec: Spec, count: int) -> None:
    first = select_operations(spec, count)
    assert len(first) == count == len(set(first))
    assert first == select_operations(spec, count)


def test_core_operations_come_first(spec: Spec) -> None:
    assert select_operations(spec, 10) == list(ALWAYS_INCLUDED[:10])
    assert set(ALWAYS_INCLUDED) <= set(select_operations(spec, 25))
    assert set(ALWAYS_INCLUDED) <= set(select_operations(spec, 50))


def test_bigger_subsets_contain_smaller_ones(spec: Spec) -> None:
    sizes = [10, 25, 50, 100]
    subsets = [set(select_operations(spec, n)) for n in sizes]
    for small, big in itertools.pairwise(subsets):
        assert small < big


def test_fill_order_is_tag_then_operation_id(spec: Spec) -> None:
    chosen = select_operations(spec, 50)
    filler = [op for op in chosen if op not in ALWAYS_INCLUDED]
    keys = [(spec.operations[op].tag, op) for op in filler]
    assert keys == sorted(keys)


def test_none_or_full_count_means_everything(spec: Spec) -> None:
    everything = select_operations(spec, None)
    assert len(everything) == 102
    assert select_operations(spec, 102) == everything


@pytest.mark.parametrize("count", [0, -1, 103])
def test_out_of_range(spec: Spec, count: int) -> None:
    with pytest.raises(ToolCountError):
        select_operations(spec, count)
