# ABOUTME: quota show/list must display cost budgets, not the unused token zeros
# ABOUTME: A customer's $20/$5 budget rendered as "Monthly Token Limit 0"

"""Cost-based policies (the wizard's recommended default) keep
monthly_token_limit=0 by design. The show/list renderers only printed token
fields, so a configured budget looked like a broken zero-limit policy."""

from claude_code_with_bedrock.cli.commands.quota import _policy_limit_cells, _policy_limit_rows
from claude_code_with_bedrock.models import EnforcementMode, PolicyType, QuotaPolicy


def _cost_policy(**kw):
    defaults = {
        "policy_type": PolicyType.DEFAULT,
        "identifier": "default",
        "monthly_token_limit": 0,
        "monthly_cost_limit": 20.0,
        "daily_cost_limit": 5.0,
        "enforcement_mode": EnforcementMode.BLOCK,
    }
    defaults.update(kw)
    return QuotaPolicy(**defaults)


def _token_policy():
    return QuotaPolicy(
        policy_type=PolicyType.DEFAULT,
        identifier="default",
        monthly_token_limit=300_000_000,
        daily_token_limit=10_000_000,
    )


def test_show_renders_budgets_for_cost_policy():
    rows = dict(_policy_limit_rows(_cost_policy()))
    assert rows.get("Monthly Budget") == "$20.00"
    assert rows.get("Daily Budget") == "$5.00"
    assert "Monthly Token Limit" not in rows, "the misleading zero token row must go"


def test_show_omits_token_thresholds_in_cost_mode():
    labels = [label for label, _ in _policy_limit_rows(_cost_policy())]
    assert "Warning (80%)" not in labels and "Critical (90%)" not in labels


def test_show_token_policy_unchanged():
    rows = dict(_policy_limit_rows(_token_policy()))
    assert "Monthly Token Limit" in rows
    assert "Daily Token Limit" in rows
    assert "Warning (80%)" in rows and "Critical (90%)" in rows


def test_list_cells_show_dollars_for_cost_policy():
    assert _policy_limit_cells(_cost_policy()) == ("$20.00", "$5.00")


def test_list_cells_daily_dash_when_no_daily_budget():
    assert _policy_limit_cells(_cost_policy(daily_cost_limit=None))[1] == "-"


def test_list_cells_token_policy_unchanged():
    monthly, daily = _policy_limit_cells(_token_policy())
    assert "$" not in monthly and "$" not in daily
