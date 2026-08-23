# ABOUTME: Regression tests for the IDC zero-binary Windows installer (install.bat)
# ABOUTME: Zero-binary packages must ship a Windows installer matching the bash install.sh

"""IDC zero-binary packages used to ship NO Windows installer at all.

The zero-binary branch of _create_installer wrote only the bash install.sh and
returned early; _create_windows_installer is only reachable from the standard
(binary) path. Meanwhile the generated README unconditionally instructs
Windows users to run `install.bat` — a file that did not exist.

These tests pin the fix: zero-binary packages ship an install.bat that mirrors
install.sh (config.json copy, ~/.aws/config [profile]/[sso-session] stanza,
Claude settings install, `aws sso login` instructions) and reference no
binaries; and the README's instructions match the IDC auth flow.
"""

from pathlib import Path

from claude_code_with_bedrock.cli.commands.package import PackageCommand
from claude_code_with_bedrock.config import Profile


def _make_zero_binary_profile(name: str = "acme-idc") -> Profile:
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


def _make_oidc_profile() -> Profile:
    return Profile(
        name="oidc",
        provider_domain="test.okta.com",
        client_id="client-1",
        credential_storage="keyring",
        aws_region="us-east-1",
        identity_pool_name="test-pool",
        auth_type="oidc",
        monitoring_enabled=False,
    )


def _generate(tmp_path: Path, profile: Profile, profile_name: str) -> Path:
    command = PackageCommand()
    command._create_installer(
        tmp_path, profile, built_executables=[], built_otel_helpers=None, profile_name=profile_name
    )
    return tmp_path / "install.bat"


class TestZeroBinaryWindowsInstaller:
    def test_install_bat_is_generated(self, tmp_path):
        """The regression: zero-binary packages contained no install.bat at all."""
        bat = _generate(tmp_path, _make_zero_binary_profile(), "acme-idc")
        assert bat.exists(), "IDC zero-binary package must ship an install.bat for Windows users"

    def test_install_bat_configures_the_packaging_profile(self, tmp_path):
        bat = _generate(tmp_path, _make_zero_binary_profile("acme-idc"), "acme-idc")
        content = bat.read_text(encoding="utf-8")

        # ~/.aws/config stanza — same names install.sh writes
        assert "[profile acme-idc]" in content
        assert "sso_session = acme-idc-session" in content
        assert "[sso-session acme-idc-session]" in content
        assert "sso_account_id = 123456789012" in content
        assert "sso_role_name = BedrockAccess" in content
        assert "sso_start_url = https://d-123456.awsapps.com/start" in content
        assert "sso_region = us-east-1" in content

        # config.json + Claude settings install steps
        assert "config.json" in content
        assert "claude-settings\\settings.json" in content

        # End-user auth instructions
        assert "aws sso login --profile acme-idc" in content
        assert "aws sts get-caller-identity --profile acme-idc" in content

    def test_install_bat_references_no_binaries(self, tmp_path):
        """Zero-binary contract: the installer must not install or invoke any
        shipped binary (an explanatory comment may still name the mode)."""
        bat = _generate(tmp_path, _make_zero_binary_profile(), "acme-idc")
        content = bat.read_text(encoding="utf-8")
        assert "credential-process.exe" not in content
        assert "otel-helper" not in content
        assert "credential_process" not in content  # no credential_process AWS-config entry either

    def test_install_bat_uses_crlf_line_endings(self, tmp_path):
        """Generated Windows scripts must carry CRLF regardless of packaging host."""
        bat = _generate(tmp_path, _make_zero_binary_profile(), "acme-idc")
        raw = bat.read_bytes()
        assert b"\r\n" in raw
        # No bare-LF lines (every newline is preceded by CR)
        assert raw.count(b"\n") == raw.count(b"\r\n")

    def test_install_bat_no_hardcoded_claudecode_for_custom_profile(self, tmp_path):
        content = _generate(tmp_path, _make_zero_binary_profile("acme-idc"), "acme-idc").read_text(encoding="utf-8")
        assert "ClaudeCode" not in content

    def test_binary_package_windows_installer_unchanged(self, tmp_path):
        """Non-zero-binary packages keep the standard Windows installer behavior
        (only generated when Windows binaries or CodeBuild are in play)."""
        command = PackageCommand()
        profile = _make_oidc_profile()
        exec_path = tmp_path / "credential-process-macos-arm64"
        exec_path.touch()
        command._create_installer(
            tmp_path, profile, built_executables=[("macos-arm64", exec_path)], built_otel_helpers=[]
        )
        assert not (tmp_path / "install.bat").exists()


class TestZeroBinaryReadme:
    def test_readme_windows_instructions_match_idc_flow(self, tmp_path):
        """README must not tell zero-binary Windows users the browser opens via
        credential-process — the flow is `aws sso login`."""
        command = PackageCommand()
        command._create_documentation(tmp_path, _make_zero_binary_profile(), "2026-01-01-000000")
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")

        assert "install.bat" in readme  # still points at the (now existing) installer
        assert "aws sso login --profile ClaudeCode" in readme
        assert "browser will open automatically" not in readme

    def test_readme_standard_profile_keeps_oidc_instructions(self, tmp_path):
        command = PackageCommand()
        command._create_documentation(tmp_path, _make_oidc_profile(), "2026-01-01-000000")
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")

        assert "browser will open automatically" in readme
        assert "aws sso login" not in readme
