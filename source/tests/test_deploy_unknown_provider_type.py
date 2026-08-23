# ABOUTME: Regression tests — an unrecognized provider_type must fail fast with a clear error
# ABOUTME: It used to silently fall back to the Okta template and die in CloudFormation

"""Unknown provider_type handling in the auth stack deploy.

``template_map.get(provider_type, "bedrock-auth-okta.yaml")`` silently mapped
any unrecognized non-None provider_type (e.g. a hand-edited profile.json with
'keycloak' or 'ping') to the Okta template. Since no parameter branch matched,
OktaDomain/OktaClientId — required template parameters with no Default —
were never passed, and CloudFormation failed with an opaque
"Parameters: [OktaDomain, OktaClientId] must have values" error. The
template-exists check never fired because the Okta template exists.

The fix validates provider_type against the template map and returns 1 with an
error naming the supported providers, before any CloudFormation call. The
``profile.provider_type or "okta"`` legacy default (None -> okta, for profiles
predating provider detection) is intentional and preserved. The same silent
fallback existed in the --show-commands copy and is fixed identically.
"""

import dataclasses
from unittest.mock import MagicMock

from claude_code_with_bedrock.cli.commands.deploy import DeployCommand
from claude_code_with_bedrock.config import Profile

SUPPORTED = ("okta", "auth0", "azure", "cognito", "google", "generic")


def _make_profile(**overrides):
    field_names = {f.name for f in dataclasses.fields(Profile)}
    defaults = {
        "name": "TestProfile",
        "provider_domain": "company.okta.com",
        "client_id": "test-client-id",
        "credential_storage": "session",
        "aws_region": "us-east-1",
        "identity_pool_name": "claude-code-test",
        "sso_enabled": True,
        "provider_type": "okta",
        "monitoring_enabled": False,
        "allowed_bedrock_regions": ["us-east-1"],
    }
    defaults.update(overrides)
    return Profile(**{k: v for k, v in defaults.items() if k in field_names})


def _console_text(console) -> str:
    return " ".join(str(call.args[0]) for call in console.print.call_args_list if call.args)


def _deploy_auth(profile):
    mock_manager = MagicMock()
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.outputs = {}
    mock_manager.deploy_stack.return_value = mock_result

    console = MagicMock()
    command = DeployCommand()
    result = command._deploy_stack("auth", profile, console, mock_manager)
    return result, mock_manager, console


class TestUnknownProviderTypeFailsFast:
    def test_returns_nonzero(self):
        result, _, _ = _deploy_auth(_make_profile(provider_type="keycloak"))
        assert result == 1

    def test_never_reaches_cloudformation(self):
        """The opaque-CFN-failure bug: deploy must fail before CloudFormation."""
        _, manager, _ = _deploy_auth(_make_profile(provider_type="keycloak"))
        manager.deploy_stack.assert_not_called()

    def test_error_names_the_bad_value_and_the_supported_providers(self):
        _, _, console = _deploy_auth(_make_profile(provider_type="keycloak"))
        text = _console_text(console)
        assert "keycloak" in text
        for provider in SUPPORTED:
            assert provider in text

    def test_other_unknown_value(self):
        result, manager, _ = _deploy_auth(_make_profile(provider_type="ping"))
        assert result == 1
        manager.deploy_stack.assert_not_called()


class TestLegacyNoneDefaultPreserved:
    """provider_type=None predates provider detection and must still mean Okta."""

    def test_none_provider_type_deploys_okta_template(self):
        result, manager, _ = _deploy_auth(_make_profile(provider_type=None))
        assert result == 0
        manager.deploy_stack.assert_called_once()
        template_path = str(manager.deploy_stack.call_args.kwargs["template_path"])
        assert "bedrock-auth-okta.yaml" in template_path

    def test_none_provider_type_passes_okta_params(self):
        _, manager, _ = _deploy_auth(_make_profile(provider_type=None))
        params = manager.deploy_stack.call_args.kwargs["parameters"]
        by_key = {p["ParameterKey"]: p["ParameterValue"] for p in params}
        assert by_key["OktaDomain"] == "company.okta.com"
        assert by_key["OktaClientId"] == "test-client-id"

    def test_explicit_okta_still_deploys(self):
        result, manager, _ = _deploy_auth(_make_profile(provider_type="okta"))
        assert result == 0
        manager.deploy_stack.assert_called_once()


class TestShowCommandsCopy:
    """_show_deployment_commands duplicated the silent okta fallback."""

    def test_unknown_provider_prints_error_not_an_okta_command(self):
        console = MagicMock()
        command = DeployCommand()
        command._show_deployment_commands("auth", _make_profile(provider_type="keycloak"), console)
        text = _console_text(console)
        assert "keycloak" in text
        assert "bedrock-auth-okta.yaml" not in text
        assert "cloudformation deploy" not in text

    def test_none_provider_type_still_prints_okta_command(self):
        console = MagicMock()
        command = DeployCommand()
        command._show_deployment_commands("auth", _make_profile(provider_type=None), console)
        text = _console_text(console)
        assert "bedrock-auth-okta.yaml" in text
        assert "cloudformation deploy" in text
