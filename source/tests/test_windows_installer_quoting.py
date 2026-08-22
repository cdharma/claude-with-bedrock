# ABOUTME: Tests the install.bat AWS-profile fallback used when the AWS CLI is absent
# ABOUTME: Building PowerShell inside batch kept breaking on nested quotes and regex

r"""Tests for the generated Windows installer's AWS-profile fallback.

This path runs only when the AWS CLI is missing (the CLI branch is used otherwise),
which is why it stayed broken for so long: most machines have the CLI.

It originally assembled a multi-statement PowerShell script inside the batch file
using ``^`` continuations. cmd.exe wraps each piece in double quotes, so any inner
quote ended the piece early and PowerShell received fragments. Two rounds of
quote-escaping fixes each moved the failure rather than removing it:

    The regular expression pattern \ is not valid.     <- -replace '\', '/'
    The term '[profile' is not recognized...           <- $section = "`n[profile ...

Meanwhile the installer printed "OK Created AWS profile" regardless and left an empty
``~/.aws/config``, so Claude Desktop's inferenceBedrockProfile pointed at a stanza
that did not exist.

It now uses plain batch: ``findstr /c:`` for the existence check and ``>>`` redirection
to append. No quoting, no regex, nothing for cmd.exe to mangle. These tests pin that
the fragile constructs stay gone.
"""

import dataclasses
from pathlib import Path

import pytest

from claude_code_with_bedrock.cli.commands.package import PackageCommand
from claude_code_with_bedrock.config import Profile


def _idc_profile() -> Profile:
    """A minimal valid IDC profile with monitoring, quota and Desktop enabled."""
    field_names = {f.name for f in dataclasses.fields(Profile)}
    defaults = {
        "name": "test-profile",
        "provider_domain": "none",
        "client_id": "none",
        "credential_storage": "session",
        "aws_region": "ap-south-1",
        "identity_pool_name": "claude-code-test",
        "sso_enabled": False,
        "auth_type": "idc",
        "idc_start_url": "https://example.awsapps.com/start",
        "idc_account_id": "123456789012",
        "idc_permission_set_name": "BedrockDeveloperAccess",
        "monitoring_enabled": True,
        "quota_monitoring_enabled": True,
        "cowork_3p_enabled": True,
        "settings_target": "user",
    }
    return Profile(**{k: v for k, v in defaults.items() if k in field_names})


@pytest.fixture
def install_bat(tmp_path) -> str:
    cmd = PackageCommand.__new__(PackageCommand)
    path = cmd._create_windows_installer(tmp_path, _idc_profile())
    assert Path(path).exists(), "installer was not written"
    return Path(path).read_text(encoding="utf-8")


def _no_cli_branch(install_bat: str) -> str:
    """The fallback branch, excluding REM commentary."""
    lines = install_bat.split("\n")
    start = next(i for i, line in enumerate(lines) if "No AWS CLI" in line)
    body = lines[start : start + 45]
    return "\n".join(line for line in body if not line.strip().upper().startswith("REM"))


class TestProfileFallbackUsesPlainBatch:
    def test_no_powershell_in_the_fallback(self, install_bat):
        """The regression: PowerShell assembled inside batch could not survive cmd's
        quote handling. Two escaping attempts failed before this was replaced."""
        assert "powershell" not in _no_cli_branch(install_bat).lower()

    def test_no_replace_regex(self, install_bat):
        """`-replace '\\', '/'` raised "The regular expression pattern \\ is not valid"
        because -replace takes a regex and a lone backslash is not one."""
        assert "-replace" not in _no_cli_branch(install_bat)

    def test_existence_check_uses_literal_findstr(self, install_bat):
        """findstr /c: matches literally, so the [ in [profile needs no escaping."""
        branch = _no_cli_branch(install_bat)
        assert 'findstr /c:"[profile' in branch

    def test_stanza_is_appended_with_redirection(self, install_bat):
        branch = _no_cli_branch(install_bat)
        assert "echo [profile" in branch, "profile header is never written"
        assert "echo region = " in branch, "region is never written"
        assert "echo credential_process = " in branch, "credential_process is never written"

    def test_credential_process_points_at_the_installed_exe(self, install_bat):
        assert "claude-code-with-bedrock\\credential-process.exe --profile" in _no_cli_branch(install_bat)

    def test_success_message_is_inside_the_write_branch(self, install_bat):
        """It used to print unconditionally, reporting success while writing nothing."""
        branch = _no_cli_branch(install_bat)
        write_at = branch.index("echo credential_process = ")
        ok_at = branch.index("OK Created AWS profile")
        assert write_at < ok_at, "success is announced before the stanza is written"

    def test_region_is_resolved_before_the_existence_check(self, install_bat):
        """The collector block also needs PROF_REGION. Setting it inside the existence
        check left "region = " empty on a re-install where the profile already existed."""
        branch = _no_cli_branch(install_bat)
        assert branch.index("PROF_REGION=") < branch.index('findstr /c:"[profile')

    def test_config_directory_is_created_first(self, install_bat):
        branch = _no_cli_branch(install_bat)
        assert 'mkdir "%USERPROFILE%\\.aws"' in branch
        assert branch.index("mkdir") < branch.index("findstr")

    def test_collector_profile_is_gated_on_the_sidecar_config(self, install_bat):
        """Only sidecar packages ship collector-config.yaml; otherwise the extra
        profile is noise."""
        branch = _no_cli_branch(install_bat)
        assert "collector-config.yaml" in branch
        assert "-collector]" in branch
