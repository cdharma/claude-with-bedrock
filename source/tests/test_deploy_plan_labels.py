# ABOUTME: Deployment-plan labels must match the auth mode — no Cognito on IDC
# ABOUTME: A customer's IDC deploy plan displayed "Authentication Stack (Cognito + IAM)"

"""IDC deploys no Cognito resources, so the plan must not say Cognito."""

from types import SimpleNamespace

from claude_code_with_bedrock.cli.commands.deploy import _auth_stack_description


def _profile(auth_type, federation="cognito"):
    return SimpleNamespace(effective_auth_type=auth_type, federation_type=federation)


def test_idc_plan_names_identity_center_not_cognito():
    label = _auth_stack_description(_profile("idc"))
    assert "Identity Center" in label
    assert "Cognito" not in label


def test_direct_sts_plan_does_not_claim_cognito():
    assert "Cognito" not in _auth_stack_description(_profile("oidc", federation="direct"))


def test_cognito_federation_keeps_its_accurate_label():
    assert "Cognito" in _auth_stack_description(_profile("oidc"))


def test_both_plan_call_sites_use_the_helper():
    """The regression: two hardcoded '(Cognito + IAM)' strings in deploy.py."""
    import inspect

    from claude_code_with_bedrock.cli.commands import deploy

    src = inspect.getsource(deploy)
    assert 'append(("auth", "Authentication Stack (Cognito + IAM)"))' not in src
    assert src.count("_auth_stack_description(profile)") >= 2
