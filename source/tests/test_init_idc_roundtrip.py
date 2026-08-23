# ABOUTME: Regression test — re-running init must preserve IAM Identity Center auth config
# ABOUTME: Guards _check_existing_deployment against dropping auth_type/sso_enabled/idc_* fields

"""Regression test: "Update existing profile" must round-trip IDC auth fields.

`_save_configuration` persists six auth fields (auth_type, sso_enabled,
idc_start_url, idc_account_id, idc_permission_set_name, sso_region) but
`_check_existing_deployment` never restored any of them. Consequences:

* the wizard's auth-method select defaulted an IDC profile back to OIDC
  (the fallback heuristic reads sso_enabled, which was also missing),
* the IDC prompts showed empty defaults instead of the saved values,
* accepting the defaults silently rewrote the profile with
  auth_type="oidc" and every idc_* field nulled.

This is the config-sync.md round-trip class (PRs #436/#619/#624).

Tests avoid any TTY: the restore path is exercised directly, and the
auth-method select is mocked to capture the ``default=`` it is offered.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ruff: noqa: E402
sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_code_with_bedrock.cli.commands.init import InitCommand
from claude_code_with_bedrock.config import Config, Profile

IDC_FIELDS = {
    "auth_type": "idc",
    "sso_enabled": False,
    "idc_start_url": "https://acme.awsapps.com/start",
    "idc_account_id": "123456789012",
    "idc_permission_set_name": "BedrockDeveloperAccess",
    "sso_region": "eu-west-1",
}


def _make_idc_profile() -> Profile:
    """A profile as `_save_configuration` writes it for an IDC deployment."""
    return Profile(
        name="test",
        provider_domain="none",  # IDC path stores "none" (no OIDC provider)
        client_id="none",
        identity_pool_name="claude-code-auth",
        credential_storage="session",
        aws_region="us-east-1",
        **IDC_FIELDS,
    )


def _rebuild_config(profile: Profile) -> dict:
    """Run _check_existing_deployment with AWS interaction stubbed out."""
    command = InitCommand()
    fake_config = Config()
    with (
        patch.object(Config, "load", return_value=fake_config),
        patch.object(fake_config, "get_profile", return_value=profile),
        # Avoid any AWS calls; pretend the stack check could not run.
        patch.object(InitCommand, "_stack_exists", side_effect=Exception("no creds")),
    ):
        return command._check_existing_deployment("test")


def test_rerun_preserves_all_idc_auth_fields():
    """Every auth field _save_configuration writes must be rebuilt."""
    rebuilt = _rebuild_config(_make_idc_profile())

    for key, expected in IDC_FIELDS.items():
        assert key in rebuilt, f"_check_existing_deployment dropped {key!r}"
        assert rebuilt[key] == expected, f"{key!r}: expected {expected!r}, got {rebuilt[key]!r}"


def test_rerun_preserves_auth_fields_for_oidc_profile():
    """OIDC profiles must round-trip too (and must NOT gain idc_* keys)."""
    profile = Profile(
        name="test",
        provider_domain="example.okta.com",
        client_id="0oa1234567890",
        identity_pool_name="claude-code-auth",
        credential_storage="keyring",
        aws_region="us-east-1",
        auth_type="oidc",
        sso_enabled=True,
    )
    rebuilt = _rebuild_config(profile)

    assert rebuilt["auth_type"] == "oidc"
    assert rebuilt["sso_enabled"] is True
    for key in ("idc_start_url", "idc_account_id", "idc_permission_set_name", "sso_region"):
        assert key not in rebuilt


def test_auth_method_select_defaults_to_idc_for_restored_profile():
    """The wizard's auth-method select must be offered default='idc'."""
    command = InitCommand()
    existing_config = _rebuild_config(_make_idc_profile())

    select_calls = []

    def fake_select(*args, **kwargs):
        select_calls.append((args, kwargs))
        prompt = MagicMock()
        prompt.ask.return_value = None  # cancel — stop the wizard at the first prompt
        return prompt

    with patch("claude_code_with_bedrock.cli.commands.init.questionary.select", side_effect=fake_select):
        result = command._gather_configuration(MagicMock(), existing_config, "test")

    assert result is None  # cancelled at the auth-method select
    assert select_calls, "auth-method select was never shown"
    first_args, first_kwargs = select_calls[0]
    assert first_args and first_args[0] == "Select authentication method:"
    assert first_kwargs.get("default") == "idc"


def test_idc_prompts_prefill_saved_values():
    """The IDC text prompts must default to the saved values, not empty strings."""
    command = InitCommand()
    existing_config = _rebuild_config(_make_idc_profile())

    text_calls = []

    def fake_select(*args, **kwargs):
        prompt = MagicMock()
        prompt.ask.return_value = "idc"  # admin re-confirms the IDC auth method
        return prompt

    def fake_text(*args, **kwargs):
        text_calls.append((args, kwargs))
        prompt = MagicMock()
        if len(text_calls) < 4:
            prompt.ask.return_value = kwargs.get("default", "")  # accept the offered default
        else:
            prompt.ask.return_value = None  # cancel after the four IDC prompts
        return prompt

    with (
        patch("claude_code_with_bedrock.cli.commands.init.questionary.select", side_effect=fake_select),
        patch("claude_code_with_bedrock.cli.commands.init.questionary.text", side_effect=fake_text),
    ):
        command._gather_configuration(MagicMock(), existing_config, "test")

    prompts = {args[0]: kwargs.get("default") for args, kwargs in text_calls}
    assert prompts["Enter your IAM Identity Center start URL:"] == IDC_FIELDS["idc_start_url"]
    assert prompts["Enter your SSO region (where Identity Center is configured):"] == IDC_FIELDS["sso_region"]
    assert prompts["Enter the AWS account ID for Bedrock access:"] == IDC_FIELDS["idc_account_id"]
    assert (
        prompts["Enter the permission set name (IAM role users will assume):"] == IDC_FIELDS["idc_permission_set_name"]
    )


def test_show_existing_deployment_describes_idc(capsys):
    """The existing-deployment summary must say IDC, not print an OIDC provider line."""
    command = InitCommand()
    existing_config = _rebuild_config(_make_idc_profile())

    command._show_existing_deployment(existing_config)

    out = capsys.readouterr().out
    assert "IAM Identity Center" in out
    assert "OIDC Provider" not in out
