# ABOUTME: Tests that the init summary describes what IDC actually creates
# ABOUTME: It claimed a Cognito Identity Pool and printed token limits for cost budgets

"""Accuracy tests for the `ccwb init` review screen.

Two classes of wrong output, both on paths a customer sees before approving a deploy:

1. IDC claimed infrastructure it does not create. bedrock-auth-idc.yaml contains no
   Cognito resource and no OIDC provider — identity_pool_name is used once, to name a
   managed policy — yet the wizard advertised "Cognito Identity Pool for
   authentication", labelled the region "(Cognito, IAM, monitoring)" and showed an
   "Identity Pool" row.

2. The quota row read monthly_limit (a token count) regardless of limit type, so a
   cost-based budget of $50/month displayed as "Monthly: 0" — the value looked like
   quota was misconfigured when it was correct.
"""

import inspect

from claude_code_with_bedrock.cli.commands.init import InitCommand

REVIEW_SRC = inspect.getsource(InitCommand._review_configuration)
GATHER_SRC = inspect.getsource(InitCommand._gather_configuration)


class TestNoFalseCognitoClaims:
    def test_resources_list_is_auth_aware(self):
        """The Cognito line must be reachable only when Cognito is really created."""
        idx_idc = REVIEW_SRC.find('auth_type") == "idc"')
        idx_cognito = REVIEW_SRC.find("Cognito Identity Pool for authentication")
        assert idx_idc != -1, "the resources list no longer branches on IDC"
        assert idx_idc < idx_cognito, "IDC must be handled before the Cognito fallback"

    def test_idc_resource_line_denies_cognito_and_oidc(self):
        assert "no Cognito pool, no OIDC provider" in REVIEW_SRC

    def test_region_prompt_does_not_mention_cognito(self):
        assert "Cognito, IAM, monitoring" not in GATHER_SRC

    def test_region_summary_row_does_not_mention_cognito(self):
        assert "Cognito, IAM, Monitoring" not in REVIEW_SRC

    def test_identity_pool_row_is_relabelled_for_idc(self):
        """On IDC the value only prefixes resource names; calling it an identity pool
        implies a Cognito resource that does not exist."""
        assert "Resource Name Prefix" in REVIEW_SRC


class TestQuotaSummaryMatchesLimitType:
    def test_cost_mode_reads_the_cost_limit(self):
        assert 'limit_type") == "cost"' in REVIEW_SRC, "quota row does not branch on limit type"
        assert "monthly_cost_limit" in REVIEW_SRC, "cost budget is never read"

    def test_cost_mode_is_rendered_as_currency(self):
        assert "/user" in REVIEW_SRC and "$" in REVIEW_SRC

    def test_token_mode_is_labelled_as_tokens(self):
        assert "tokens (" in REVIEW_SRC, "token limits must be labelled to avoid ambiguity"
