# ABOUTME: Tests that cost-based quota limits are persisted to the policy record
# ABOUTME: The policy model was token-only, so a cost budget resolved as unlimited

"""Tests for cost-limit persistence in quota policies.

`QuotaPolicy`'s own docstring said "Policies define token and cost limits", but no cost
fields existed. A cost-based deployment therefore wrote a default policy carrying only
`monthly_token_limit: 0`, with no `monthly_cost_limit` attribute at all.

That was inert while `ENABLE_FINEGRAINED_QUOTAS=false`, because the quota Lambda builds
its policy from environment variables in that mode. But `resolve_quota_for_user` reads
the DynamoDB record when fine-grained quotas are enabled, defaults a missing
`monthly_cost_limit` to 0, and enforces only when `> 0` — so turning fine-grained
quotas on would have silently made every user unlimited, losing the budget entirely.
"""

from decimal import Decimal

from claude_code_with_bedrock.models import EnforcementMode, PolicyType, QuotaPolicy


def _cost_policy(**kw):
    defaults = {
        "policy_type": PolicyType.DEFAULT,
        "identifier": "default",
        "monthly_token_limit": 0,
        "monthly_cost_limit": 50.0,
        "daily_cost_limit": 5.0,
        "enforcement_mode": EnforcementMode.ALERT,
    }
    defaults.update(kw)
    return QuotaPolicy(**defaults)


class TestCostLimitsPersist:
    def test_model_accepts_cost_limits(self):
        p = _cost_policy()
        assert p.monthly_cost_limit == 50.0
        assert p.daily_cost_limit == 5.0

    def test_cost_limits_are_serialised(self):
        """The regression: these keys were absent from the stored item."""
        item = _cost_policy().to_dynamodb_item()
        assert "monthly_cost_limit" in item, "cost budget would not reach DynamoDB"
        assert "daily_cost_limit" in item

    def test_cost_limits_are_decimal_not_float(self):
        """boto3 raises TypeError for float on a Number attribute."""
        item = _cost_policy().to_dynamodb_item()
        assert isinstance(item["monthly_cost_limit"], Decimal)
        assert isinstance(item["daily_cost_limit"], Decimal)

    def test_decimal_avoids_binary_float_artefacts(self):
        """Decimal(str(x)) rather than Decimal(x), or 0.1 becomes 0.1000000000000000055."""
        item = _cost_policy(monthly_cost_limit=0.1).to_dynamodb_item()
        assert str(item["monthly_cost_limit"]) == "0.1"

    def test_round_trip_preserves_cost_limits(self):
        item = _cost_policy().to_dynamodb_item()
        back = QuotaPolicy.from_dynamodb_item(item)
        assert back.monthly_cost_limit == 50.0
        assert back.daily_cost_limit == 5.0

    def test_token_only_policy_omits_cost_keys(self):
        """A token deployment must not write zero cost caps, which the Lambda would
        read as 'no cost limit' anyway but which muddy the record."""
        item = QuotaPolicy(
            policy_type=PolicyType.DEFAULT, identifier="default", monthly_token_limit=300_000_000
        ).to_dynamodb_item()
        assert "monthly_cost_limit" not in item
        assert "daily_cost_limit" not in item

    def test_round_trip_of_a_token_only_policy_keeps_cost_none(self):
        item = QuotaPolicy(
            policy_type=PolicyType.DEFAULT, identifier="default", monthly_token_limit=300_000_000
        ).to_dynamodb_item()
        back = QuotaPolicy.from_dynamodb_item(item)
        assert back.monthly_cost_limit is None
        assert back.daily_cost_limit is None


class TestManagerSignatures:
    def test_create_policy_accepts_cost_limits(self):
        import inspect

        from claude_code_with_bedrock.quota_policies import QuotaPolicyManager

        params = inspect.signature(QuotaPolicyManager.create_policy).parameters
        assert "monthly_cost_limit" in params, "deploy cannot persist a cost budget"
        assert "daily_cost_limit" in params

    def test_update_policy_accepts_cost_limits(self):
        """Otherwise `ccwb quota` can create a cost policy but never change it."""
        import inspect

        from claude_code_with_bedrock.quota_policies import QuotaPolicyManager

        params = inspect.signature(QuotaPolicyManager.update_policy).parameters
        assert "monthly_cost_limit" in params
        assert "daily_cost_limit" in params
