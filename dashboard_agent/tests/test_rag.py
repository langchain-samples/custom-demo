"""Unit tests for the in-memory RAG datasearch."""

from dashboard_agent.rag import search


def test_egypt_query_returns_egypt_reports():
    res = search("impact of humanitarian aid in Egypt last quarter", k=3)
    assert res, "expected results"
    assert res[0]["region"] == "Egypt"
    ids = {r["id"] for r in res}
    # Both Egypt quarters should surface so the agent can compute a delta.
    assert "egypt-aid-q2-2026" in ids
    assert "egypt-aid-q1-2026" in ids


def test_iran_query_returns_displaced_report_first():
    res = search("available resources for displaced families in Iran", k=3)
    assert res[0]["id"] == "iran-displaced-2026"
    assert "resources" in res[0]["data"]


def test_canada_query_returns_wash_report_first():
    res = search("water scarcity and sanitation needs in Canada", k=3)
    assert res[0]["id"] == "canada-wash-2026"
    assert "water_access_national_pct" in res[0]["data"]


def test_results_are_ranked_by_score():
    res = search("Egypt humanitarian aid", k=5)
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)


def test_irrelevant_query_returns_little_or_nothing():
    res = search("quantum computing gpu benchmarks", k=3)
    # Nothing in the corpus is about this; scores should be filtered out.
    assert res == [] or all(r["score"] < 0.2 for r in res)


def test_results_carry_text_and_structured_data():
    res = search("Egypt aid impact", k=1)
    top = res[0]
    assert top["text"]  # groundable prose
    assert isinstance(top["data"], dict) and top["data"]  # chartable numbers
    assert top["source"]  # citable
