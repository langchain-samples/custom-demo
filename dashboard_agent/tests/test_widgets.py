"""Unit tests for widget schema validation."""

import pytest
from pydantic import ValidationError

from dashboard_agent.widgets import validate_widget


def test_valid_kpi():
    w = validate_widget({"type": "kpi", "title": "People reached", "value": "2.4M", "trend": "up"})
    assert w["type"] == "kpi" and w["value"] == "2.4M"


def test_valid_bar_chart():
    w = validate_widget(
        {
            "type": "bar",
            "title": "Funding by sector",
            "series": [{"name": "Q2", "points": [{"label": "Health", "value": 28000000}]}],
        }
    )
    assert w["type"] == "bar"
    assert w["series"][0]["points"][0]["value"] == 28000000


def test_valid_line_chart_multi_series():
    w = validate_widget(
        {
            "type": "line",
            "title": "Trend",
            "series": [
                {"name": "2025", "points": [{"label": "Jan", "value": 1}]},
                {"name": "2026", "points": [{"label": "Jan", "value": 2}]},
            ],
        }
    )
    assert len(w["series"]) == 2


def test_valid_table():
    w = validate_widget(
        {
            "type": "table",
            "title": "Resources",
            "columns": ["A", "B"],
            "rows": [["1", "2"], ["3", "4"]],
        }
    )
    assert len(w["rows"]) == 2


def test_valid_text():
    w = validate_widget({"type": "text", "title": "Key findings", "content": "- a\n- b"})
    assert w["type"] == "text"


def test_pie_must_have_single_series():
    with pytest.raises(ValidationError):
        validate_widget(
            {
                "type": "pie",
                "title": "x",
                "series": [
                    {"name": "a", "points": [{"label": "z", "value": 1}]},
                    {"name": "b", "points": [{"label": "y", "value": 2}]},
                ],
            }
        )


def test_table_rows_must_match_columns():
    with pytest.raises(ValidationError):
        validate_widget({"type": "table", "title": "t", "columns": ["a", "b"], "rows": [["1"]]})


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        validate_widget({"type": "scatter", "title": "t"})


def test_missing_required_field_rejected():
    with pytest.raises(ValidationError):
        validate_widget({"type": "kpi", "title": "no value here"})


def test_empty_series_rejected():
    with pytest.raises(ValidationError):
        validate_widget({"type": "bar", "title": "t", "series": []})
