from __future__ import annotations

import pytest

from zotero_local_bilingual.pages import parse_pages


@pytest.mark.parametrize("value", [None, "", "  \t"])
def test_empty_selection_means_all_physical_pages(value):
    assert parse_pages(value, 4) == [0, 1, 2, 3]


def test_ranges_are_sorted_deduplicated_and_zero_based():
    assert parse_pages("5, 1 - 3, 2, 5", 7) == [0, 1, 2, 4]


def test_open_range_endpoints_use_physical_page_count():
    assert parse_pages("-3, 5-", 7) == [0, 1, 2, 4, 5, 6]


@pytest.mark.parametrize("value", [
    "0", "8", "2-8", "0-2", "3-1", "4-2", "-0", "1--3", "--2",
    "1,", ",1", "1,,2", "-", "a", "1.5", "+1", "1;2", "1 2", "1-2x",
])
def test_invalid_selection_fails_instead_of_silently_expanding(value):
    with pytest.raises(ValueError):
        parse_pages(value, 7)


@pytest.mark.parametrize("page_count", [0, -2, True, 2.5])
def test_invalid_page_count_is_rejected(page_count):
    with pytest.raises(ValueError):
        parse_pages(None, page_count)
