# ABOUTME: Regression tests for IDC+quota sidecar collector-config identity substitution
# ABOUTME: The email collection must cover the IDC+quota path, not only zero-binary mode

"""IDC sidecar packages bake a STATIC identity into collector-config.yaml.

collector-config-idc.yaml carries ${USER_EMAIL}/${USER_NAME}/${DEPARTMENT}/
${TEAM_ID}/${COST_CENTER}/${ORGANIZATION} placeholders that must be resolved at
package time. The email collection used to live exclusively under the IDC
zero-binary branch of handle(), so the IDC+quota path passed
idc_user_email=None into _generate_collector_config — whose substitution block
was guarded by `if idc_user_email is not None:` — and the shipped
collector-config.yaml still contained the literal placeholders (only ${REGION}
was replaced). The collector then exported "${USER_EMAIL}" verbatim as the
user.email resource attribute for every metric.

Two layers pinned here:
  1. _generate_collector_config must never ship unresolved placeholders, even
     when no email is known (anonymous fallbacks, per otel-attribution-chain).
  2. handle() must collect the user email for the IDC+quota sidecar path so the
     baked identity is the real user, not the anonymous fallback.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cleo.testers.command_tester import CommandTester

from claude_code_with_bedrock.cli.commands.package import PackageCommand
from claude_code_with_bedrock.config import Config, Profile

IDC_PLACEHOLDERS = (
    "${USER_EMAIL}",
    "${USER_NAME}",
    "${DEPARTMENT}",
    "${TEAM_ID}",
    "${COST_CENTER}",
    "${ORGANIZATION}",
    "${REGION}",
)


class TestGenerateCollectorConfigPlaceholderSafety:
    """_generate_collector_config must resolve every placeholder regardless of caller input."""

    def test_none_email_resolves_all_placeholders_with_fallbacks(self, tmp_path):
        """idc_user_email=None (the IDC+quota handle() bug) must not ship literal ${...}."""
        cmd = PackageCommand()
        cmd._generate_collector_config(
            output_dir=tmp_path,
            template_name="collector-config-idc.yaml",
            region="us-east-1",
            idc_user_email=None,
        )
        content = (tmp_path / "collector-config.yaml").read_text(encoding="utf-8")
        for placeholder in IDC_PLACEHOLDERS:
            assert placeholder not in content, f"unresolved placeholder shipped: {placeholder}"
        # Anonymous fallback per the otel-attribution-chain contract.
        assert "unknown@example.com" in content

    def test_empty_email_resolves_all_placeholders_with_fallbacks(self, tmp_path):
        """Empty email (auto-detect failed, no prompt) keeps the historical fallback."""
        cmd = PackageCommand()
        cmd._generate_collector_config(
            output_dir=tmp_path,
            template_name="collector-config-idc.yaml",
            region="us-east-1",
            idc_user_email="",
        )
        content = (tmp_path / "collector-config.yaml").read_text(encoding="utf-8")
        for placeholder in IDC_PLACEHOLDERS:
            assert placeholder not in content
        assert "unknown@example.com" in content

    def test_real_email_still_substituted(self, tmp_path):
        """Sanity: a supplied email is baked in (no behavior change for the good path)."""
        cmd = PackageCommand()
        cmd._generate_collector_config(
            output_dir=tmp_path,
            template_name="collector-config-idc.yaml",
            region="us-east-1",
            idc_user_email="alice@example.com",
            otel_resource_attributes="department=platform,team.id=infra",
        )
        content = (tmp_path / "collector-config.yaml").read_text(encoding="utf-8")
        assert "alice@example.com" in content
        assert "platform" in content
        assert "infra" in content
        for placeholder in IDC_PLACEHOLDERS:
            assert placeholder not in content

    def test_oidc_template_untouched_by_identity_logic(self, tmp_path):
        """The OIDC template has no identity placeholders — only ${REGION} is replaced."""
        cmd = PackageCommand()
        cmd._generate_collector_config(
            output_dir=tmp_path,
            template_name="collector-config.yaml",
            region="us-east-1",
        )
        content = (tmp_path / "collector-config.yaml").read_text(encoding="utf-8")
        assert "${REGION}" not in content
        assert "unknown@example.com" not in content


def _make_idc_quota_sidecar_profile() -> Profile:
    """IDC auth + quota endpoint + sidecar monitoring → NOT zero-binary."""
    return Profile(
        name="idcq",
        provider_domain="",
        client_id="",
        credential_storage="keyring",
        aws_region="us-east-1",
        identity_pool_name="",
        sso_enabled=False,
        auth_type="idc",
        monitoring_enabled=True,
        monitoring_mode="sidecar",
        quota_api_endpoint="https://quota.example.com/check",
        idc_start_url="https://d-123456.awsapps.com/start",
        idc_account_id="123456789012",
        idc_permission_set_name="BedrockAccess",
        cowork_3p_enabled=False,
    )


@pytest.fixture
def mock_config():
    profile = _make_idc_quota_sidecar_profile()
    config = MagicMock(spec=Config)
    config.get_profile.return_value = profile
    config.active_profile = "idcq"
    return config


class TestIDCQuotaSidecarIdentityCollection:
    """handle() must collect the IDC user email for the IDC+quota sidecar path."""

    def _run_package(self, tmp_path, monkeypatch, mock_config, sts_arn):
        monkeypatch.chdir(tmp_path)

        sts = MagicMock()
        sts.get_caller_identity.return_value = {"Arn": sts_arn}

        command = PackageCommand()
        tester = CommandTester(command)

        with (
            patch("claude_code_with_bedrock.config.Config.load", return_value=mock_config),
            patch("claude_code_with_bedrock.cli.commands.package._is_interactive", return_value=False),
            patch("boto3.client", return_value=sts),
            patch.object(PackageCommand, "_build_executable", return_value=Path("credential-process-macos-arm64")),
            patch.object(PackageCommand, "_build_otel_helper", return_value=Path("otel-helper-macos-arm64")),
            patch.object(PackageCommand, "_build_otelcol"),
        ):
            result = tester.execute("--target-platform macos-arm64 --legacy --skip-validation")
        return result, tester

    def test_collector_config_bakes_detected_sts_email(self, tmp_path, monkeypatch, mock_config):
        """The regression: IDC+quota used to pass idc_user_email=None, shipping ${USER_EMAIL}."""
        result, tester = self._run_package(
            tmp_path,
            monkeypatch,
            mock_config,
            sts_arn="arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_BedrockAccess/carol@example.com",
        )
        assert result == 0, tester.io.fetch_output()

        collector_configs = list(tmp_path.glob("dist/idcq/*/collector-config.yaml"))
        assert collector_configs, "IDC+quota sidecar package must ship collector-config.yaml"
        content = collector_configs[0].read_text(encoding="utf-8")

        # The email detected from the IDC STS session name must be baked in —
        # not the anonymous fallback, and never a literal placeholder.
        assert "carol@example.com" in content
        assert "unknown@example.com" not in content
        for placeholder in IDC_PLACEHOLDERS:
            assert placeholder not in content

    def test_undetectable_email_falls_back_to_anonymous(self, tmp_path, monkeypatch, mock_config):
        """No @ in the session name and no TTY → anonymous fallback, still no placeholders."""
        result, tester = self._run_package(
            tmp_path,
            monkeypatch,
            mock_config,
            sts_arn="arn:aws:iam::123456789012:user/machine-account",
        )
        assert result == 0, tester.io.fetch_output()

        collector_configs = list(tmp_path.glob("dist/idcq/*/collector-config.yaml"))
        assert collector_configs
        content = collector_configs[0].read_text(encoding="utf-8")
        for placeholder in IDC_PLACEHOLDERS:
            assert placeholder not in content
