# ABOUTME: Regression tests for IDC zero-binary installer AWS profile naming
# ABOUTME: install.sh must configure the SAME AWS profile that config.json/settings.json reference

"""IDC zero-binary packages ship three artifacts that must agree on one name:

- config.json          — keyed on the packaging profile name
- settings.json        — env.AWS_PROFILE set to the packaging profile name
- install.sh           — writes ~/.aws/config [profile <name>] / [sso-session <name>-session]
                          and prints 'aws sso login --profile <name>' instructions

The installer used to hardcode 'ClaudeCode'. For any deployment whose packaging
profile was not named 'ClaudeCode' (e.g. 'acme-idc'), settings.json pointed
Claude Code at an AWS profile the installer never created, and end-user auth
failed with 'profile not found'. These tests pin the consistency contract.
"""

import inspect
import json
from pathlib import Path

from claude_code_with_bedrock.cli.commands.package import PackageCommand
from claude_code_with_bedrock.config import Profile


def _make_idc_zero_binary_profile(name: str = "acme-idc") -> Profile:
    """IDC auth, no quota endpoint → zero-binary packaging mode."""
    return Profile(
        name=name,
        provider_domain="",
        client_id="",
        credential_storage="keyring",
        aws_region="us-east-1",
        identity_pool_name="",
        sso_enabled=False,
        auth_type="idc",
        monitoring_enabled=False,
        quota_api_endpoint="",
        idc_start_url="https://d-123456.awsapps.com/start",
        idc_account_id="123456789012",
        idc_permission_set_name="BedrockAccess",
    )


def _generate_installer(tmp_path: Path, profile: Profile, **kwargs) -> str:
    command = PackageCommand()
    installer_path = command._create_installer(
        tmp_path, profile, built_executables=[], built_otel_helpers=None, **kwargs
    )
    return installer_path.read_text(encoding="utf-8")


class TestZeroBinaryInstallerProfileName:
    """install.sh must use the packaging profile name, not a hardcoded 'ClaudeCode'."""

    def test_installer_uses_packaging_profile_name(self, tmp_path):
        content = _generate_installer(tmp_path, _make_idc_zero_binary_profile("acme-idc"), profile_name="acme-idc")

        # ~/.aws/config entries written by the heredoc
        assert "[profile acme-idc]" in content
        assert "sso_session = acme-idc-session" in content
        assert "[sso-session acme-idc-session]" in content

        # sed cleanup of stale entries must target the same names it writes
        assert "/^\\[profile acme-idc\\]/,/^$/d" in content
        assert "/^\\[sso-session acme-idc-session\\]/,/^$/d" in content

        # Printed end-user instructions
        assert "aws sso login --profile acme-idc" in content
        assert "aws sts get-caller-identity --profile acme-idc" in content

    def test_installer_has_no_hardcoded_claudecode(self, tmp_path):
        """Regression: with a non-default packaging profile, 'ClaudeCode' must not appear anywhere."""
        content = _generate_installer(tmp_path, _make_idc_zero_binary_profile("acme-idc"), profile_name="acme-idc")
        assert "ClaudeCode" not in content

    def test_installer_defaults_to_claudecode_for_backward_compat(self, tmp_path):
        """Callers that don't pass profile_name keep the historical 'ClaudeCode' name."""
        content = _generate_installer(tmp_path, _make_idc_zero_binary_profile("ClaudeCode"))
        assert "[profile ClaudeCode]" in content
        assert "[sso-session ClaudeCode-session]" in content
        assert "aws sso login --profile ClaudeCode" in content


class TestZeroBinaryArtifactConsistency:
    """The AWS profile referenced by settings.json/config.json must exist after install.sh runs."""

    def test_settings_aws_profile_matches_installer_profile(self, tmp_path):
        profile_name = "acme-idc"
        profile = _make_idc_zero_binary_profile(profile_name)
        command = PackageCommand()

        command._create_claude_settings(tmp_path, profile, profile_name=profile_name, is_idc_zero_binary=True)
        settings = json.loads((tmp_path / "claude-settings" / "settings.json").read_text(encoding="utf-8"))
        aws_profile = settings["env"]["AWS_PROFILE"]

        content = _generate_installer(tmp_path, profile, profile_name=profile_name)

        # Whatever name settings.json points Claude Code at, install.sh must create it
        # and tell the user to log in to it.
        assert f"[profile {aws_profile}]" in content
        assert f"sso_session = {aws_profile}-session" in content
        assert f"[sso-session {aws_profile}-session]" in content
        assert f"aws sso login --profile {aws_profile}" in content

    def test_config_json_key_matches_installer_profile(self, tmp_path):
        profile_name = "acme-idc"
        profile = _make_idc_zero_binary_profile(profile_name)
        command = PackageCommand()

        command._create_config(tmp_path, profile, federation_identifier="", profile_name=profile_name)
        config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        (config_key,) = config.keys()

        content = _generate_installer(tmp_path, profile, profile_name=profile_name)
        assert f"[profile {config_key}]" in content

    def test_installer_still_bakes_idc_settings(self, tmp_path):
        """Interpolating the profile name must not disturb the other heredoc fields."""
        content = _generate_installer(tmp_path, _make_idc_zero_binary_profile("acme-idc"), profile_name="acme-idc")
        assert "sso_account_id = 123456789012" in content
        assert "sso_role_name = BedrockAccess" in content
        assert "sso_start_url = https://d-123456.awsapps.com/start" in content


class TestHandleWiring:
    """Source-level guard: handle() must thread the packaging profile name into the installer."""

    def test_handle_passes_profile_name_to_create_installer(self):
        src = inspect.getsource(PackageCommand.handle)
        call_lines = [line for line in src.splitlines() if "self._create_installer(" in line]
        assert call_lines, "handle() no longer calls _create_installer — packaging is broken."
        for line in call_lines:
            assert "profile_name=profile_name" in line, (
                "handle() calls _create_installer without profile_name — the IDC zero-binary "
                "installer would fall back to a hardcoded 'ClaudeCode' AWS profile while "
                "config.json/settings.json use the real packaging profile name."
            )
