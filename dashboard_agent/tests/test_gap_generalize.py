"""Unit tests for _generalize_gap (broadening an over-qualified data gap)."""

from dashboard_agent.assistant_setup import _generalize_gap


def test_trims_segment_qualifiers():
    assert _generalize_gap("Customer dwell time by store section") == "Customer dwell time"
    assert _generalize_gap("conversion rate by traffic source") == "conversion rate"
    assert _generalize_gap("on-time delivery rate across carriers") == "on-time delivery rate"


def test_keeps_already_general_or_short_roots():
    # Already general — unchanged.
    assert _generalize_gap("net promoter score") == "net promoter score"
    assert _generalize_gap("revenue") == "revenue"
    # Trimming would leave a single word ("sales"), so keep the fuller phrase.
    assert _generalize_gap("sales per region") == "sales per region"
