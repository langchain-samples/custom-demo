"""Unit tests for the plain helpers in `evaluators.py`.

`evals/` had no tests at all, which is how `_read_skill_first` shipped returning
False for the most ordinary transcript there is. These need no key and no network,
so they run in the normal suite.
"""

from __future__ import annotations

from evals.evaluators import _read_skill_first


def test_a_directory_listing_before_computing_counts_as_reading_the_skill():
    assert _read_skill_first(["ls", "execute"]) is True


def test_computing_before_any_file_read_does_not_count():
    assert _read_skill_first(["execute", "ls"]) is False


def test_reading_a_file_and_nothing_else_counts():
    """The case the old version got wrong.

    `read_file` was in both the filesystem set and the data set, so it was treated as
    its own disqualifier and this returned False.
    """
    assert _read_skill_first(["glob", "read_file"]) is True


def test_no_file_access_at_all_never_counts():
    assert _read_skill_first([]) is False
    assert _read_skill_first(["push_widget"]) is False


def test_read_file_alone_cannot_decide_it_either_way():
    """`read_file` reads skills AND data, so it marks no boundary.

    Asserted so that putting it back into either set fails here rather than silently
    reintroducing the bug.
    """
    assert _read_skill_first(["read_file"]) is False
    assert _read_skill_first(["read_file", "execute"]) is False
