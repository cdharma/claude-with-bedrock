# ABOUTME: Regression tests — monitoring custom-domain ALB JWT params must cover ALL 6 provider types
# ABOUTME: google/generic used to match no branch, silently deploying an HTTPS listener with no JWT auth

"""ALB JWT-validation parameters for the monitoring (otel-collector) stack.

When ``monitoring_config.custom_domain`` is set, the deploy builds
OidcIssuerUrl/OidcJwksEndpoint/OidcClientId parameters so the collector's
HTTPS listener validates JWTs. Before the fix, only 4 of the 6 documented
provider types (azure, okta, auth0, cognito) had a branch: 'google' and
'generic' fell through, ``oidc_issuer`` stayed empty, and the
``if oidc_issuer and oidc_jwks`` guard silently omitted the parameters. In
otel-collector.yaml an empty OidcIssuerUrl makes ``HasJwtAuth`` false, so
google/generic profiles got an HTTPS telemetry listener with NO JWT
validation — with no warning at deploy time. The same silent skip hid an
azure provider_domain without a tenant GUID and a cognito profile without a
pool ID.

These tests pin:
- google gets its fixed well-known issuer/JWKS endpoints
- generic gets profile.oidc_issuer_url / profile.oidc_jwks_uri (and does not
  require provider_domain, which generic profiles may leave unset)
- any OIDC profile whose issuer/JWKS cannot be built triggers a loud warning
  instead of a silent skip (deploy still proceeds — the HTTP:80 listener is
  unauthenticated anyway, so JWT on :443 is defense-in-depth, not the sole
  auth barrier)
- the four previously working providers are unchanged (backward compat)
"""

import dataclasses
from unittest.mock import MagicMock, patch

from claude_code_with_bedrock.cli.commands.deploy import DeployCommand
from claude_code_with_bedrock.config import Profile

NETWORKING_STACK = "claude-code-test-networking"
NETWORKING_OUTPUTS = {"VpcId": "vpc-123", "SubnetIds": "subnet-1,subnet-2"}
CUSTOM_DOMAIN_CONFIG = {"custom_domain": "otel.example.com"}


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
        "monitoring_enabled": True,
        "quota_monitoring_enabled": False,
        "monitoring_config": dict(CUSTOM_DOMAIN_CONFIG),
    }
    defaults.update(overrides)
    return Profile(**{k: v for k, v in defaults.items() if k in field_names})


def _console_text(console) -> str:
    return " ".join(str(call.args[0]) for call in console.print.call_args_list if call.args)


def _deploy_monitoring(profile):
    """Invoke _deploy_stack('monitoring', ...) with mocked AWS boundaries."""
    mock_manager = MagicMock()
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.outputs = {}
    mock_manager.deploy_stack.return_value = mock_result

    console = MagicMock()

    def fake_get_stack_outputs(stack_name, region):
        if stack_name == NETWORKING_STACK:
            return dict(NETWORKING_OUTPUTS)
        return {}

    command = DeployCommand()
    with (
        patch(
            "claude_code_with_bedrock.cli.commands.deploy.get_stack_outputs",
            side_effect=fake_get_stack_outputs,
        ),
        patch.object(DeployCommand, "_ensure_ecs_service_linked_role"),
        patch("boto3.client", MagicMock()),
    ):
        result = command._deploy_stack("monitoring", profile, console, mock_manager)

    return result, mock_manager, console


def _params_by_key(manager) -> dict:
    params = manager.deploy_stack.call_args.kwargs["parameters"]
    return {p["ParameterKey"]: p["ParameterValue"] for p in params}


WARNING_MARKER = "WITHOUT JWT validation"


class TestGoogleProvider:
    """google matched no branch before the fix -> no JWT params, silently."""

    def test_google_gets_fixed_issuer_and_jwks(self):
        profile = _make_profile(provider_type="google", provider_domain="company.com")
        result, manager, _ = _deploy_monitoring(profile)
        assert result == 0
        by_key = _params_by_key(manager)
        assert by_key["OidcIssuerUrl"] == "https://accounts.google.com"
        assert by_key["OidcJwksEndpoint"] == "https://www.googleapis.com/oauth2/v3/certs"
        assert by_key["OidcClientId"] == "test-client-id"

    def test_google_does_not_warn(self):
        profile = _make_profile(provider_type="google", provider_domain="company.com")
        _, _, console = _deploy_monitoring(profile)
        assert WARNING_MARKER not in _console_text(console)


class TestGenericProvider:
    """generic matched no branch before the fix -> no JWT params, silently."""

    def test_generic_uses_profile_issuer_and_jwks(self):
        profile = _make_profile(
            provider_type="generic",
            provider_domain="auth.example.com",
            oidc_issuer_url="https://auth.example.com",
            oidc_jwks_uri="https://auth.example.com/protocol/openid-connect/certs",
        )
        result, manager, _ = _deploy_monitoring(profile)
        assert result == 0
        by_key = _params_by_key(manager)
        assert by_key["OidcIssuerUrl"] == "https://auth.example.com"
        assert by_key["OidcJwksEndpoint"] == "https://auth.example.com/protocol/openid-connect/certs"
        assert by_key["OidcClientId"] == "test-client-id"

    def test_generic_without_provider_domain_still_gets_jwt_params(self):
        """The old outer guard `if provider_type and provider_domain` excluded
        generic profiles whose provider_domain is unset — the endpoints come
        from explicit profile fields, not from a domain."""
        profile = _make_profile(
            provider_type="generic",
            provider_domain="",
            oidc_issuer_url="https://auth.example.com",
            oidc_jwks_uri="https://auth.example.com/jwks",
        )
        result, manager, _ = _deploy_monitoring(profile)
        assert result == 0
        by_key = _params_by_key(manager)
        assert by_key["OidcIssuerUrl"] == "https://auth.example.com"
        assert by_key["OidcJwksEndpoint"] == "https://auth.example.com/jwks"

    def test_generic_issuer_without_scheme_gets_https_prefix(self):
        profile = _make_profile(
            provider_type="generic",
            oidc_issuer_url="auth.example.com/realms/corp",
            oidc_jwks_uri="https://auth.example.com/realms/corp/jwks",
        )
        _, manager, _ = _deploy_monitoring(profile)
        assert _params_by_key(manager)["OidcIssuerUrl"] == "https://auth.example.com/realms/corp"


class TestSilentSkipNowWarns:
    """custom_domain + OIDC auth but no derivable issuer/JWKS must warn loudly."""

    def test_generic_without_issuer_warns_and_omits_jwt_params(self):
        profile = _make_profile(
            provider_type="generic",
            provider_domain="",
            oidc_issuer_url=None,
            oidc_jwks_uri=None,
        )
        result, manager, console = _deploy_monitoring(profile)
        assert result == 0  # deploy proceeds — JWT on :443 is defense-in-depth
        by_key = _params_by_key(manager)
        assert "OidcIssuerUrl" not in by_key
        assert WARNING_MARKER in _console_text(console)

    def test_azure_domain_without_tenant_guid_warns(self):
        profile = _make_profile(provider_type="azure", provider_domain="login.microsoftonline.com")
        result, manager, console = _deploy_monitoring(profile)
        assert result == 0
        assert "OidcIssuerUrl" not in _params_by_key(manager)
        assert WARNING_MARKER in _console_text(console)

    def test_cognito_without_pool_id_warns(self):
        profile = _make_profile(
            provider_type="cognito",
            provider_domain="myprefix.auth.us-east-1.amazoncognito.com",
            cognito_user_pool_id=None,
        )
        result, manager, console = _deploy_monitoring(profile)
        assert result == 0
        assert "OidcIssuerUrl" not in _params_by_key(manager)
        assert WARNING_MARKER in _console_text(console)

    def test_warning_names_the_provider_type(self):
        profile = _make_profile(provider_type="generic", oidc_issuer_url=None, oidc_jwks_uri=None)
        _, _, console = _deploy_monitoring(profile)
        assert "generic" in _console_text(console)


class TestNonOidcAndNoCustomDomainStayQuiet:
    def test_no_custom_domain_means_no_jwt_params_and_no_warning(self):
        profile = _make_profile(monitoring_config={})
        result, manager, console = _deploy_monitoring(profile)
        assert result == 0
        assert "OidcIssuerUrl" not in _params_by_key(manager)
        assert WARNING_MARKER not in _console_text(console)

    def test_auth_none_does_not_warn(self):
        """auth 'none' has no JWT to validate — warning would be pure noise."""
        profile = _make_profile(auth_type="none", sso_enabled=False, provider_type=None, provider_domain="")
        result, manager, console = _deploy_monitoring(profile)
        assert result == 0
        assert "OidcIssuerUrl" not in _params_by_key(manager)
        assert WARNING_MARKER not in _console_text(console)

    def test_auth_idc_does_not_warn(self):
        """IDC has no JWT either — telemetry auth is SigV4/service-token."""
        profile = _make_profile(auth_type="idc", provider_type=None, provider_domain="")
        result, manager, console = _deploy_monitoring(profile)
        assert result == 0
        assert "OidcIssuerUrl" not in _params_by_key(manager)
        assert WARNING_MARKER not in _console_text(console)


class TestExistingProvidersUnchanged:
    """Backward compat: the four previously covered providers keep their params."""

    def test_okta(self):
        profile = _make_profile(provider_type="okta", provider_domain="company.okta.com")
        _, manager, console = _deploy_monitoring(profile)
        by_key = _params_by_key(manager)
        assert by_key["OidcIssuerUrl"] == "https://company.okta.com/oauth2/default"
        assert by_key["OidcJwksEndpoint"] == "https://company.okta.com/oauth2/default/v1/keys"
        assert WARNING_MARKER not in _console_text(console)

    def test_azure_with_tenant_guid(self):
        tid = "12345678-1234-1234-1234-123456789abc"
        profile = _make_profile(provider_type="azure", provider_domain=f"login.microsoftonline.com/{tid}/v2.0")
        _, manager, _ = _deploy_monitoring(profile)
        by_key = _params_by_key(manager)
        assert by_key["OidcIssuerUrl"] == f"https://login.microsoftonline.com/{tid}/v2.0"
        assert by_key["OidcJwksEndpoint"] == f"https://login.microsoftonline.com/{tid}/discovery/v2.0/keys"

    def test_auth0(self):
        profile = _make_profile(provider_type="auth0", provider_domain="company.auth0.com")
        _, manager, _ = _deploy_monitoring(profile)
        by_key = _params_by_key(manager)
        assert by_key["OidcIssuerUrl"] == "https://company.auth0.com/"
        assert by_key["OidcJwksEndpoint"] == "https://company.auth0.com/.well-known/jwks.json"

    def test_cognito_with_pool_id(self):
        profile = _make_profile(
            provider_type="cognito",
            provider_domain="myprefix.auth.us-east-1.amazoncognito.com",
            cognito_user_pool_id="us-east-1_AbCdEfGhI",
        )
        _, manager, _ = _deploy_monitoring(profile)
        by_key = _params_by_key(manager)
        assert by_key["OidcIssuerUrl"] == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbCdEfGhI"
        assert by_key["OidcJwksEndpoint"] == (
            "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbCdEfGhI/.well-known/jwks.json"
        )
