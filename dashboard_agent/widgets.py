"""Dashboard widget schemas.

These Pydantic models are the contract between the agent and the frontend
canvas. The agent emits validated widget specs (via the `push_widget` tool);
the frontend renders each one into the persistent dashboard. Validation happens
server-side so a malformed spec from the model is rejected before it ever
reaches the browser.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class DataPoint(BaseModel):
    label: str = Field(..., description="Category / x-axis label, e.g. 'Health' or 'Jun'")
    value: float = Field(..., description="Numeric value for this point")


class Series(BaseModel):
    name: str = Field(..., description="Series name shown in the legend")
    points: list[DataPoint] = Field(..., min_length=1)


class KpiWidget(BaseModel):
    type: Literal["kpi"] = "kpi"
    title: str = Field(..., description="Short metric label, e.g. 'People reached'")
    value: str = Field(..., description="The headline value, pre-formatted, e.g. '2.4M' or '68%'")
    unit: str | None = Field(None, description="Optional unit/suffix, e.g. 'people' or 'USD'")
    delta: str | None = Field(None, description="Optional change vs. baseline, e.g. '+26% vs Q1'")
    trend: Literal["up", "down", "flat"] | None = Field(
        None, description="Direction of delta for color coding"
    )
    description: str | None = Field(None, description="One-line context for the metric")


class ChartWidget(BaseModel):
    type: Literal["bar", "line", "pie"]
    title: str
    series: list[Series] = Field(..., min_length=1)
    x_label: str | None = None
    y_label: str | None = None

    @model_validator(mode="after")
    def _pie_single_series(self) -> ChartWidget:
        if self.type == "pie" and len(self.series) != 1:
            raise ValueError("pie charts must have exactly one series")
        return self


class TableWidget(BaseModel):
    type: Literal["table"] = "table"
    title: str
    columns: list[str] = Field(..., min_length=1)
    rows: list[list[str]] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _rows_match_columns(self) -> TableWidget:
        width = len(self.columns)
        for i, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"row {i} has {len(row)} cells but there are {width} columns")
        return self


class TextWidget(BaseModel):
    type: Literal["text"] = "text"
    title: str
    content: str = Field(..., description="Markdown-ish narrative / key findings")


Widget = Annotated[
    KpiWidget | ChartWidget | TableWidget | TextWidget,
    Field(discriminator="type"),
]


class WidgetEnvelope(BaseModel):
    """Wrapper used to validate an arbitrary incoming widget dict."""

    widget: Widget


def validate_widget(spec: dict) -> dict:
    """Validate a raw widget dict and return its normalized dict form.

    Raises pydantic.ValidationError if the spec is malformed.
    """
    return WidgetEnvelope(widget=spec).widget.model_dump(exclude_none=True)


WIDGET_TYPES = ["kpi", "bar", "line", "pie", "table", "text"]
