# ABOUTME: Regression tests for doctor's monitoring detection in end-user config.json
# ABOUTME: Monitoring checks must trigger for configs written by ccwb package

"""doctor keyed its monitoring checks on a key nobody wrote.

Checks 6 (otel-helper), 7 (proxy status), and 9 (live proxy health) decided
"is monitoring configured?" solely from per-profile otel_collector_endpoint in
the installed config.json — but _create_config never wrote that key, so for
every package produced by ccwb those failure branches were unreachable: a
broken monitoring install reported "skipped / Monitoring not configured".

The fix is two-sided and both halves are pinned here:
  * package.py writes monitoring_enabled / monitoring_mode (and
    otel_collector_endpoint when resolvable) into config.json, and
  * doctor keys detection on monitoring_enabled OR otel_collector_endpoint
    (via _monitoring_configured), so old and new configs both work.
"""

import json
import sys
from unittest.mock import MagicMock, patch

from claude_code_with_bedrock.cli.commands.doctor import _monitoring_configured, run_doctor
from claude_code_with_bedrock.cli.commands.package import PackageCommand
from claude_code_with_bedrock.config import Profile

BINARY_NAME = "credential-process.exe" if sys.platform == "win32" else "credential-process"


def _install(tmp_path, profile_config: dict, with_otel_helper: bool = False):
    """Lay down a minimal install: binary + config.json (new 'profiles' format)."""
    install_dir = tmp_path / "claude-code-with-bedrock"
    install_dir.mkdir()
    (install_dir / BINARY_NAME).write_text("binary")
    if with_otel_helper:
        otel_name = "otel-helper.exe" if sys.platform == "win32" else "otel-helper"
        (install_dir / otel_name).write_text("binary")
    (install_dir / "config.json").write_text(json.dumps({"profiles": {"default": profile_config}}))
    return install_dir


def _run(tmp_path):
    """run_doctor with binary subprocess calls stubbed out (no real execution)."""

    def mock_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        return result

    with patch("claude_code_with_bedrock.cli.commands.doctor.subprocess.run", side_effect=mock_run):
        return run_doctor(home=tmp_path)


def _check(checks, name):
    return next(c for c in checks if c.name == name)


class TestMonitoringConfiguredHelper:
    def test_none_config(self):
        assert _monitoring_configured(None) is False

    def test_empty_config(self):
        assert _monitoring_configured({}) is False

    def test_endpoint_key(self):
        cfg = {"profiles": {"p": {"otel_collector_endpoint": "https://collector.example.com"}}}
        assert _monitoring_configured(cfg) is True

    def test_monitoring_enabled_key(self):
        cfg = {"profiles": {"p": {"monitoring_enabled": True}}}
        assert _monitoring_configured(cfg) is True

    def test_old_format_top_level_profiles(self):
        cfg = {"ClaudeCode": {"monitoring_enabled": True}}
        assert _monitoring_configured(cfg) is True

    def test_monitoring_enabled_false_is_not_configured(self):
        cfg = {"profiles": {"p": {"monitoring_enabled": False}}}
        assert _monitoring_configured(cfg) is False

    def test_non_dict_profile_entries_ignored(self):
        cfg = {"profiles": {"p": "not-a-dict"}}
        assert _monitoring_configured(cfg) is False


class TestOtelHelperCheckDetection:
    def test_monitoring_enabled_key_without_otel_helper_fails(self, tmp_path):
        """The regression: this fail branch was unreachable for ccwb-built packages."""
        _install(tmp_path, {"monitoring_enabled": True, "monitoring_mode": "central"})
        checks = _run(tmp_path)
        otel_check = _check(checks, "otel-helper")
        assert otel_check.status == "fail", (
            f"monitoring_enabled config with no otel-helper must FAIL, got {otel_check.status!r} ({otel_check.message})"
        )

    def test_endpoint_key_without_otel_helper_fails(self, tmp_path):
        """The historical detection key keeps working."""
        _install(tmp_path, {"otel_collector_endpoint": "https://collector.example.com:4318"})
        checks = _run(tmp_path)
        assert _check(checks, "otel-helper").status == "fail"

    def test_config_without_monitoring_keys_skips(self, tmp_path):
        """Backward compat: old configs with no monitoring keys still skip."""
        _install(tmp_path, {"provider_domain": "company.okta.com", "aws_region": "us-west-2"})
        checks = _run(tmp_path)
        assert _check(checks, "otel-helper").status == "skipped"

    def test_otel_helper_present_passes(self, tmp_path):
        _install(tmp_path, {"monitoring_enabled": True}, with_otel_helper=True)
        checks = _run(tmp_path)
        assert _check(checks, "otel-helper").status == "pass"


class TestPackagedConfigDrivesDoctor:
    """End-to-end: a config.json written by _create_config must trip doctor's
    monitoring checks. Fails if EITHER side of the contract regresses (package
    stops writing the keys, or doctor stops reading them)."""

    def test_monitoring_profile_package_then_doctor_fail(self, tmp_path):
        install_dir = tmp_path / "claude-code-with-bedrock"
        install_dir.mkdir()
        (install_dir / BINARY_NAME).write_text("binary")

        profile = Profile(
            name="default",
            provider_domain="test.okta.com",
            client_id="client-1",
            credential_storage="keyring",
            aws_region="us-east-1",
            identity_pool_name="test-pool",
            monitoring_enabled=True,
            monitoring_mode="sidecar",
        )
        PackageCommand()._create_config(install_dir, profile, "us-east-1:pool-id", "cognito", "default")

        checks = _run(tmp_path)
        otel_check = _check(checks, "otel-helper")
        assert otel_check.status == "fail", (
            "A ccwb-packaged monitoring config with a missing otel-helper must fail doctor's "
            f"otel-helper check, got {otel_check.status!r} ({otel_check.message})"
        )

    def test_monitoring_disabled_package_then_doctor_skips(self, tmp_path):
        install_dir = tmp_path / "claude-code-with-bedrock"
        install_dir.mkdir()
        (install_dir / BINARY_NAME).write_text("binary")

        profile = Profile(
            name="default",
            provider_domain="test.okta.com",
            client_id="client-1",
            credential_storage="keyring",
            aws_region="us-east-1",
            identity_pool_name="test-pool",
            monitoring_enabled=False,
        )
        PackageCommand()._create_config(install_dir, profile, "us-east-1:pool-id", "cognito", "default")

        checks = _run(tmp_path)
        assert _check(checks, "otel-helper").status == "skipped"
