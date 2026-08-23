# ABOUTME: Contract tests keeping _create_config (Python) in sync with Go ProfileConfig
# ABOUTME: Every emitted config.json key needs a Go JSON tag (or a documented exemption)

"""config.json contract: package.py writer ↔ Go/Python binary readers.

`ccwb package` writes config.json (_create_config) and the credential-process
binaries read it (Go: go/internal/config/config.go; Python:
credential_provider/__main__.py). There was no test keeping the two in sync,
and verified drift existed:

  * oidc_prompt — consumed by both binaries, never emitted by _create_config.
  * The Okta authorization server — consumed under DIFFERENT keys (Python
    reads "okta_auth_server", Go reads "okta_auth_server_id") and _create_config
    emitted neither, so a profile with okta_auth_server set produced binaries
    hitting the wrong Okta endpoints.
  * cross_region_profile / selected_model — emitted with no Go tag (benign:
    encoding/json ignores unknown keys, but documented below).

See .claude/rules/config-sync.md.
"""

import json
import re
from pathlib import Path

from claude_code_with_bedrock.cli.commands.package import PackageCommand
from claude_code_with_bedrock.config import Profile

GO_CONFIG_PATH = Path(__file__).resolve().parents[3] / "go" / "internal" / "config" / "config.go"

# Keys _create_config writes that intentionally have NO Go ProfileConfig tag.
# Every entry must carry a justification; anything else missing a tag is drift.
PYTHON_ONLY_KEYS = {
    # Python credential_provider reads this key; the Go binary reads the
    # okta_auth_server_id twin, which _create_config emits alongside it.
    "okta_auth_server",
    # Informational for installers/settings generation only — neither binary
    # reads it (model/region routing is driven via settings.json env vars).
    "cross_region_profile",
    "selected_model",
}


def _go_json_tags() -> set[str]:
    text = GO_CONFIG_PATH.read_text(encoding="utf-8")
    return set(re.findall(r'json:"([A-Za-z0-9_]+)', text))


def _emit(profile: Profile, out_dir: Path, federation_identifier="us-east-1:pool-id", federation_type="cognito"):
    out_dir.mkdir(parents=True, exist_ok=True)
    PackageCommand()._create_config(out_dir, profile, federation_identifier, federation_type, "ClaudeCode")
    return json.loads((out_dir / "config.json").read_text(encoding="utf-8"))["ClaudeCode"]


def _base_profile(**overrides) -> Profile:
    kwargs = {
        "name": "test",
        "provider_domain": "test.okta.com",
        "client_id": "client-1",
        "credential_storage": "keyring",
        "aws_region": "us-east-1",
        "identity_pool_name": "test-pool",
        "monitoring_enabled": False,
    }
    kwargs.update(overrides)
    return Profile(**kwargs)


def _all_variants() -> list[tuple[str, Profile, str, str]]:
    """(label, profile, federation_type, federation_identifier) covering every
    conditional emission branch in _create_config."""
    return [
        (
            "okta-cognito",
            _base_profile(provider_type="okta", okta_auth_server="custom-as", redirect_port=8401),
            "cognito",
            "us-east-1:pool-id",
        ),
        (
            "azure-direct",
            _base_profile(
                provider_domain="login.microsoftonline.com/tenant/v2.0",
                provider_type="azure",
                azure_auth_mode="certificate",
                client_certificate_path="certs/cert.pem",
                client_certificate_key_path="certs/key.pem",
                oidc_prompt="",
                federation_type="direct",
                max_session_duration=43200,
            ),
            "direct",
            "arn:aws:iam::123456789012:role/BedrockFederated",
        ),
        (
            "google",
            _base_profile(
                provider_domain="accounts.google.com",
                provider_type="google",
                client_secret="non-confidential-secret",
            ),
            "cognito",
            "us-east-1:pool-id",
        ),
        (
            "generic-oidc",
            _base_profile(
                provider_domain="auth.example.com",
                provider_type="generic",
                oidc_issuer_url="https://auth.example.com",
                oidc_authorization_endpoint="https://auth.example.com/authorize",
                oidc_token_endpoint="https://auth.example.com/token",
                oidc_jwks_uri="https://auth.example.com/jwks",
                oidc_thumbprint="ab" * 20,
            ),
            "cognito",
            "us-east-1:pool-id",
        ),
        (
            "cognito-pool",
            _base_profile(
                provider_domain="test.auth.us-east-1.amazoncognito.com",
                provider_type="cognito",
                cognito_user_pool_id="us-east-1_abc123",
                cross_region_profile="us",
                selected_model="us.anthropic.claude-sonnet-4-20250514-v1:0",
            ),
            "cognito",
            "us-east-1:pool-id",
        ),
        (
            "idc-quota-monitoring",
            _base_profile(
                provider_domain="",
                client_id="",
                identity_pool_name="",
                sso_enabled=False,
                auth_type="idc",
                idc_start_url="https://d-123456.awsapps.com/start",
                idc_account_id="123456789012",
                idc_permission_set_name="BedrockAccess",
                quota_api_endpoint="https://quota.example.com/check",
                quota_fail_mode="closed",
                monitoring_enabled=True,
                monitoring_mode="sidecar",
            ),
            "cognito",
            "",
        ),
        (
            "oidc-central-monitoring",
            _base_profile(
                provider_type="okta",
                monitoring_enabled=True,
                monitoring_mode="central",
                otel_collector_endpoint="https://collector.example.com:4318",
            ),
            "cognito",
            "us-east-1:pool-id",
        ),
    ]


class TestConfigGoContract:
    def test_every_emitted_key_has_go_tag_or_documented_exemption(self, tmp_path):
        """Any key _create_config writes must exist as a Go JSON tag, or be a
        documented Python-only key. Failing here means config-sync.md drift:
        add the field to go/internal/config/config.go (with a JSON tag) or
        document the exemption above."""
        go_tags = _go_json_tags()
        assert go_tags, f"could not parse JSON tags from {GO_CONFIG_PATH}"

        emitted: set[str] = set()
        for label, profile, federation_type, federation_identifier in _all_variants():
            emitted |= set(
                _emit(
                    profile,
                    tmp_path / label,
                    federation_identifier=federation_identifier,
                    federation_type=federation_type,
                ).keys()
            )

        undocumented = emitted - go_tags - PYTHON_ONLY_KEYS
        assert not undocumented, (
            f"config.json keys with no Go ProfileConfig JSON tag and no documented exemption: "
            f"{sorted(undocumented)}. Mirror them in go/internal/config/config.go or add a "
            f"justified entry to PYTHON_ONLY_KEYS (see .claude/rules/config-sync.md)."
        )

    def test_python_only_exemptions_are_still_emitted_somewhere(self, tmp_path):
        """Guard the allowlist against going stale: every exemption must still
        be a key _create_config actually writes."""
        emitted: set[str] = set()
        for label, profile, federation_type, federation_identifier in _all_variants():
            emitted |= set(
                _emit(
                    profile,
                    tmp_path / label,
                    federation_identifier=federation_identifier,
                    federation_type=federation_type,
                ).keys()
            )
        stale = PYTHON_ONLY_KEYS - emitted
        assert not stale, f"PYTHON_ONLY_KEYS entries no longer emitted by _create_config: {sorted(stale)}"


class TestConsumedFieldsEmitted:
    """Fields the binaries consume must actually be written by _create_config."""

    def test_okta_auth_server_emitted_under_both_reader_keys(self, tmp_path):
        """Python reads okta_auth_server, Go reads okta_auth_server_id — a
        profile with the value set must serve both binaries (regression: neither
        key was emitted, so packaged binaries hit the wrong Okta endpoints)."""
        profile = _base_profile(provider_type="okta", okta_auth_server="custom-as")
        cfg = _emit(profile, tmp_path / "okta")
        assert cfg.get("okta_auth_server") == "custom-as"
        assert cfg.get("okta_auth_server_id") == "custom-as"

    def test_okta_auth_server_omitted_when_unset(self, tmp_path):
        """Empty means Org authorization server for both binaries — omit the keys
        so legacy defaulting keeps working."""
        profile = _base_profile(provider_type="okta", okta_auth_server="")
        cfg = _emit(profile, tmp_path / "okta-default")
        assert "okta_auth_server" not in cfg
        assert "okta_auth_server_id" not in cfg

    def test_oidc_prompt_empty_string_emitted(self, tmp_path):
        """'' is meaningful (suppress the prompt param for silent SSO) and must
        survive packaging — both binaries default to select_account when the
        key is absent."""
        profile = _base_profile(provider_type="azure", oidc_prompt="")
        cfg = _emit(profile, tmp_path / "azure-silent")
        assert "oidc_prompt" in cfg
        assert cfg["oidc_prompt"] == ""

    def test_oidc_prompt_value_emitted(self, tmp_path):
        profile = _base_profile(provider_type="azure", oidc_prompt="login")
        cfg = _emit(profile, tmp_path / "azure-login")
        assert cfg["oidc_prompt"] == "login"

    def test_oidc_prompt_absent_when_not_configured(self, tmp_path):
        profile = _base_profile(provider_type="azure", oidc_prompt=None)
        cfg = _emit(profile, tmp_path / "azure-none")
        assert "oidc_prompt" not in cfg

    def test_monitoring_keys_emitted_for_sidecar(self, tmp_path):
        """doctor and --explain key on these; sidecar always exports to localhost."""
        profile = _base_profile(monitoring_enabled=True, monitoring_mode="sidecar")
        cfg = _emit(profile, tmp_path / "sidecar")
        assert cfg["monitoring_enabled"] is True
        assert cfg["monitoring_mode"] == "sidecar"
        assert cfg["otel_collector_endpoint"] == "http://localhost:4318"

    def test_monitoring_keys_emitted_for_central_with_endpoint(self, tmp_path):
        profile = _base_profile(
            monitoring_enabled=True,
            monitoring_mode="central",
            otel_collector_endpoint="https://collector.example.com:4318",
        )
        cfg = _emit(profile, tmp_path / "central")
        assert cfg["monitoring_enabled"] is True
        assert cfg["monitoring_mode"] == "central"
        assert cfg["otel_collector_endpoint"] == "https://collector.example.com:4318"

    def test_monitoring_keys_absent_when_disabled(self, tmp_path):
        """Backward compat: monitoring-off packages look exactly like before."""
        profile = _base_profile(monitoring_enabled=False)
        cfg = _emit(profile, tmp_path / "disabled")
        assert "monitoring_enabled" not in cfg
        assert "monitoring_mode" not in cfg
        assert "otel_collector_endpoint" not in cfg
