# ABOUTME: Regression tests for Okta issuer derivation from profile.okta_auth_server
# ABOUTME: Guards quota JWT authorizer / ALB / web search issuer parity with the Go credential-process

"""Okta issuer must honor profile.okta_auth_server (custom authorization servers).

The packaged Go credential-process builds its token endpoints from
``profile.okta_auth_server`` (source/go/internal/provider/endpoints.go), so a
custom authorization server (e.g. "aus1b2c3") mints tokens whose ``iss`` is
``https://<domain>/oauth2/aus1b2c3``. Before the fix, deploy.py hardcoded every
CLI-side Okta issuer to ``/oauth2/default``, so the quota API Gateway JWT
authorizer (and ALB / web search JWT validation) rejected every token with 401.

These tests pin the fix: all Okta issuer construction flows through the single
``_okta_issuer`` helper, which uses ``okta_auth_server`` when set and falls
back to "default" (the historical behavior) when unset/empty.
"""

from unittest.mock import Mock

from claude_code_with_bedrock.cli.commands.deploy import (
    DeployCommand,
    _discover_oidc_endpoints,
    _okta_issuer,
    _websearch_discovery_url,
)
from claude_code_with_bedrock.config import Profile


def _okta_profile(**overrides) -> Profile:
    data = {
        "name": "test",
        "provider_domain": "company.okta.com",
        "client_id": "client123",
        "credential_storage": "keyring",
        "aws_region": "us-east-1",
        "identity_pool_name": "ccwb",
        "provider_type": "okta",
        "sso_enabled": True,
    }
    data.update(overrides)
    return Profile.from_dict(data)


class TestResolveOidcConfigOktaAuthServer:
    """_resolve_oidc_config must build the issuer from okta_auth_server."""

    def setup_method(self):
        self.command = DeployCommand()

    def test_custom_auth_server_issuer(self):
        """okta_auth_server='aus1b2c3' -> issuer ends with /oauth2/aus1b2c3."""
        profile = _okta_profile(okta_auth_server="aus1b2c3")
        issuer, client_id = self.command._resolve_oidc_config(profile)
        assert issuer == "https://company.okta.com/oauth2/aus1b2c3"
        assert client_id == "client123"

    def test_empty_auth_server_defaults_to_default(self):
        """Empty okta_auth_server keeps today's /oauth2/default (backward compat)."""
        profile = _okta_profile(okta_auth_server="")
        issuer, _ = self.command._resolve_oidc_config(profile)
        assert issuer == "https://company.okta.com/oauth2/default"

    def test_legacy_profile_without_field_defaults_to_default(self):
        """Old saved profiles (no okta_auth_server key) must load and keep /oauth2/default."""
        profile = _okta_profile()  # from_dict without the key -> dataclass default ""
        assert profile.okta_auth_server == ""
        issuer, _ = self.command._resolve_oidc_config(profile)
        assert issuer == "https://company.okta.com/oauth2/default"

    def test_whitespace_auth_server_defaults_to_default(self):
        profile = _okta_profile(okta_auth_server="   ")
        issuer, _ = self.command._resolve_oidc_config(profile)
        assert issuer == "https://company.okta.com/oauth2/default"

    def test_custom_auth_server_replaces_existing_default_segment(self):
        """provider_domain stored as full default issuer still honors the custom server."""
        profile = _okta_profile(
            provider_domain="https://company.okta.com/oauth2/default",
            okta_auth_server="aus1b2c3",
        )
        issuer, _ = self.command._resolve_oidc_config(profile)
        assert issuer == "https://company.okta.com/oauth2/aus1b2c3"

    def test_no_double_append_when_domain_is_full_issuer(self):
        """Pre-existing behavior: /oauth2/default in provider_domain isn't doubled."""
        profile = _okta_profile(provider_domain="https://company.okta.com/oauth2/default")
        issuer, _ = self.command._resolve_oidc_config(profile)
        assert issuer == "https://company.okta.com/oauth2/default"

    def test_mock_profile_without_auth_server_still_defaults(self):
        """Legacy Mock-shaped profiles (okta_auth_server not a str) fall back to default."""
        profile = Mock()
        profile.sso_enabled = True
        profile.provider_type = "okta"
        profile.provider_domain = "company.okta.com"
        profile.client_id = "abc123"
        issuer, _ = self.command._resolve_oidc_config(profile)
        assert issuer == "https://company.okta.com/oauth2/default"

    def test_non_okta_providers_unaffected(self):
        """auth0/azure/cognito issuer derivation must ignore okta_auth_server."""
        auth0 = _okta_profile(
            provider_type="auth0",
            provider_domain="company.auth0.com",
            okta_auth_server="aus1b2c3",
        )
        issuer, _ = self.command._resolve_oidc_config(auth0)
        assert issuer == "https://company.auth0.com/"

        azure = _okta_profile(
            provider_type="azure",
            provider_domain="login.microsoftonline.com/tenant-id/v2.0",
            okta_auth_server="aus1b2c3",
        )
        issuer, _ = self.command._resolve_oidc_config(azure)
        assert issuer == "https://login.microsoftonline.com/tenant-id/v2.0"

        cognito = _okta_profile(
            provider_type="cognito",
            cognito_user_pool_id="us-east-1_abc123",
            okta_auth_server="aus1b2c3",
        )
        issuer, _ = self.command._resolve_oidc_config(cognito)
        assert issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc123"


class TestOktaIssuerHelperConsistency:
    """Every CLI-side Okta issuer/discovery URL must agree with _okta_issuer."""

    def test_helper_custom_server(self):
        assert _okta_issuer(_okta_profile(okta_auth_server="aus1b2c3")) == ("https://company.okta.com/oauth2/aus1b2c3")

    def test_helper_default_server(self):
        assert _okta_issuer(_okta_profile()) == "https://company.okta.com/oauth2/default"

    def test_helper_strips_scheme_and_trailing_slash(self):
        profile = _okta_profile(provider_domain="https://company.okta.com/", okta_auth_server="aus1b2c3")
        assert _okta_issuer(profile) == "https://company.okta.com/oauth2/aus1b2c3"

    def test_websearch_discovery_url_honors_auth_server(self):
        profile = _okta_profile(okta_auth_server="aus1b2c3", web_search_enabled=True)
        url = _websearch_discovery_url(profile)
        assert url == "https://company.okta.com/oauth2/aus1b2c3/.well-known/openid-configuration"

    def test_websearch_discovery_url_default_unchanged(self):
        profile = _okta_profile(web_search_enabled=True)
        url = _websearch_discovery_url(profile)
        assert url == "https://company.okta.com/oauth2/default/.well-known/openid-configuration"

    def test_discover_oidc_endpoints_fallback_honors_auth_server(self, monkeypatch):
        """Bootstrap OIDC discovery (offline fallback) builds endpoints from the same issuer."""
        import urllib.request

        def _fail(*args, **kwargs):
            raise OSError("offline")

        monkeypatch.setattr(urllib.request, "urlopen", _fail)
        endpoints = _discover_oidc_endpoints(_okta_profile(okta_auth_server="aus1b2c3"))
        assert endpoints["issuer"] == "https://company.okta.com/oauth2/aus1b2c3"
        assert endpoints["token_endpoint"] == ("https://company.okta.com/oauth2/aus1b2c3/v1/token")
        assert endpoints["jwks_uri"] == "https://company.okta.com/oauth2/aus1b2c3/v1/keys"

    def test_alb_jwks_shape_matches_issuer(self):
        """ALB OIDC params derive JWKS as <issuer>/v1/keys — pin the custom-server shape."""
        issuer = _okta_issuer(_okta_profile(okta_auth_server="aus1b2c3"))
        assert f"{issuer}/v1/keys" == "https://company.okta.com/oauth2/aus1b2c3/v1/keys"
