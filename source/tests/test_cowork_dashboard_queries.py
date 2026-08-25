# ABOUTME: Cowork dashboard per-user widgets must not use PromQL
# ABOUTME: PromQL returned zero series for metric-filter-derived metrics

"""The per-user and per-model widgets were PromQL charts and rendered "No data
found" while the metrics demonstrably existed: a direct query to CloudWatch's
Prometheus-compatible API returned HTTP 200 with zero series, even at a
timestamp where get-metric-statistics proved a datapoint. Every widget that
worked used a standard metric query. These now use Metrics Insights SQL
(dynamic GROUP BY on a dimension) or metric math, both verified live.
"""

import json
from pathlib import Path

TEMPLATE = Path(__file__).parent.parent.parent / "deployment" / "infrastructure" / "cowork-dashboard.yaml"

PER_USER_WIDGETS = {
    "Top Users by Token Usage",
    "Estimated Cost by User (USD)",
    "Session Turns by User",
    "Cost by Model",
    "Active Users",
}


def _dashboard() -> dict:
    text = TEMPLATE.read_text(encoding="utf-8")
    body = text[text.find("DashboardBody:") :]
    raw = "\n".join(line[12:] if line.startswith(" " * 12) else line for line in body.splitlines()[1:])
    raw = raw.split("\nOutputs:")[0].replace("${MetricsRegion}", "ap-south-1")
    return json.loads(raw)


def _widget(title: str) -> dict:
    for w in _dashboard()["widgets"]:
        if w.get("properties", {}).get("title") == title:
            return w
    raise AssertionError(f"widget {title!r} not found")


def test_dashboard_body_is_valid_json():
    assert _dashboard()["widgets"], "dashboard body must parse and contain widgets"


def test_no_widget_uses_promql():
    """The regression: PromQL returns nothing for metric-filter metrics."""
    assert "PromQL" not in TEMPLATE.read_text(encoding="utf-8")


def test_per_user_widgets_group_by_a_dimension():
    for title in PER_USER_WIDGETS:
        metrics = _widget(title)["properties"]["metrics"]
        expr = next(m[0]["expression"] for m in metrics if isinstance(m[0], dict))
        assert "GROUP BY" in expr, f"{title} must group by a dimension"
        assert "user_email" in expr or "model" in expr, f"{title} groups by no known dimension"
        assert 'FROM "ClaudeCoWork"' in expr, f"{title} must query the ClaudeCoWork namespace"


def test_per_user_widgets_are_standard_metric_widgets():
    """Only 'metric' widgets support Metrics Insights expressions."""
    for title in PER_USER_WIDGETS:
        assert _widget(title)["type"] == "metric"


def test_avg_cost_per_turn_guards_division_by_zero():
    metrics = _widget("Avg Cost Per Turn (USD)")["properties"]["metrics"]
    expr = next(m[0]["expression"] for m in metrics if isinstance(m[0], dict) and "expression" in m[0])
    assert "IF(" in expr and "> 0" in expr, "cost/turns must not divide by zero when there are no turns"
