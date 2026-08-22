# ABOUTME: Regression tests for the install.bat PowerShell profile-writing fallback
# ABOUTME: Nested double quotes inside the -Command string broke cmd.exe parsing

r"""Tests for the generated Windows installer's AWS-profile fallback.

``install.bat`` builds a ``powershell -NoProfile -Command`` invocation from
``^``-continued pieces, each wrapped in double quotes by cmd.exe. The profile
existence check previously embedded a further double-quoted regex:

    "if ($existing -notmatch \"\\[profile $profileName\\]\") { ... }"

cmd.exe terminated the outer string at that inner quote, so PowerShell received
``-notmatch \[profile`` with no operand and aborted with a cascade of parse
errors — "You must provide a value expression following the '-notmatch' operator",
"Unexpected token '\[profile'", and so on. No AWS profile was written, yet the
installer still printed "Installation complete" and listed the profile as
available.

This path only runs when the AWS CLI is absent (the CLI branch is used otherwise),
which is why it survived: most machines have the CLI.
"""

import dataclasses
from pathlib import Path

import pytest

from claude_code_with_bedrock.cli.commands.package import PackageCommand
from claude_code_with_bedrock.config import Profile


def _idc_profile() -> Profile:
    """A minimal valid IDC profile, mirroring tests/cli/commands/test_deploy_matrix.py."""
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
        "monitoring_enabled": False,
        "quota_monitoring_enabled": False,
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


class TestProfileCheckQuoting:
    def test_notmatch_operand_is_not_double_quoted(self, install_bat):
        """The regression: the ``-notmatch`` operand was a double-quoted string.

        Nested ``"`` is tolerated by cmd.exe in some positions — the ``$section``
        assignment needs it for `` `n `` interpolation and works — but as the operand
        of ``-notmatch`` it left PowerShell with a bare token and no value expression.
        Assert only that this specific operand is not double-quoted.
        """
        for line in install_bat.splitlines():
            if "-notmatch" not in line:
                continue
            operand = line.split("-notmatch", 1)[1].lstrip()
            assert not operand.startswith('"'), f"-notmatch operand must not be double-quoted:\n{line}"

    def test_notmatch_uses_single_quoted_regex(self, install_bat):
        """Single quotes keep the regex intact through cmd.exe."""
        notmatch_lines = [line for line in install_bat.splitlines() if "-notmatch" in line]
        assert notmatch_lines, "profile existence check disappeared from install.bat"
        for line in notmatch_lines:
            assert "'\\[profile " in line, f"expected a single-quoted regex, got: {line}"

    def test_profile_regex_still_escapes_the_bracket(self, install_bat):
        """``[`` is a regex metacharacter. Unescaped, ``-notmatch`` never matches, so
        every re-install would append a duplicate profile stanza."""
        for line in install_bat.splitlines():
            if "-notmatch" in line:
                assert "\\[profile " in line, f"bracket not escaped for the regex: {line}"

    def test_success_message_interpolates_the_profile_name(self, install_bat):
        """Single-quoted PowerShell strings do not interpolate, so the old message
        printed a literal $profileName."""
        for line in install_bat.splitlines():
            if "OK Created AWS profile" in line:
                assert "'$profileName'" not in line, f"message will print the variable name literally: {line}"

    def test_balanced_double_quotes_on_powershell_pieces(self, install_bat):
        """Each ^-continued piece must contain an even number of unescaped quotes."""
        for line in install_bat.splitlines():
            stripped = line.strip()
            if not stripped.startswith('"') or "$profileName" not in stripped:
                continue
            unescaped = stripped.replace('\\"', "")
            assert unescaped.count('"') % 2 == 0, f"unbalanced quotes in: {stripped}"
