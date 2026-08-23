# ABOUTME: Regression tests for the otel-helper build gate in ccwb package
# ABOUTME: Monitoring-enabled packages must fail (exit 1) when otel-helper builds are missing

"""A monitoring-enabled package without an otel-helper is silently broken.

Both build paths swallow otel-helper failures — the PyInstaller path catches
the exception and prints a yellow warning, the Go path `continue`s past the
failed binary — and no later check inspected built_otel_helpers. A package
whose otel-helper failed for one or all platforms therefore printed
"✓ Package created successfully!" and exited 0, shipping users a bundle whose
telemetry never works (otelHeadersHelper points at a binary that isn't there).

These tests pin the gate: when profile.monitoring_enabled (excluding IDC
zero-binary), every platform that received a credential-process binary must
also have an otel-helper, or packaging exits non-zero.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cleo.testers.command_tester import CommandTester

from claude_code_with_bedrock.cli.commands.package import PackageCommand
from claude_code_with_bedrock.config import Config, Profile


def _make_monitoring_profile(**overrides) -> Profile:
    kwargs = {
        "name": "test",
        "provider_domain": "test.okta.com",
        "client_id": "test-client-id",
        "credential_storage": "keyring",
        "aws_region": "us-east-1",
        "identity_pool_name": "test-pool",
        "allowed_bedrock_regions": ["us-east-1"],
        "monitoring_enabled": True,
        "monitoring_mode": "central",
        "otel_collector_endpoint": "https://collector.example.com:4318",
        "enable_codebuild": False,
        "cowork_3p_enabled": False,
    }
    kwargs.update(overrides)
    return Profile(**kwargs)


@pytest.fixture
def mock_config():
    def _factory(profile):
        config = MagicMock(spec=Config)
        config.get_profile.return_value = profile
        config.active_profile = profile.name
        return config

    return _factory


def _run_package(monkeypatch, tmp_path, config, otel_helper_result):
    """Run `ccwb package` with the credential-process build succeeding and the
    otel-helper build behaving per otel_helper_result (Path or Exception)."""
    monkeypatch.chdir(tmp_path)

    command = PackageCommand()
    tester = CommandTester(command)

    if isinstance(otel_helper_result, Exception):
        otel_patch = patch.object(PackageCommand, "_build_otel_helper", side_effect=otel_helper_result)
    else:
        otel_patch = patch.object(PackageCommand, "_build_otel_helper", return_value=otel_helper_result)

    with (
        patch("claude_code_with_bedrock.config.Config.load", return_value=config),
        patch("claude_code_with_bedrock.cli.commands.package._is_interactive", return_value=False),
        patch.object(
            PackageCommand,
            "_resolve_federation",
            return_value=("cognito", "us-east-1:fake-pool-id", None),
        ),
        patch.object(PackageCommand, "_build_executable", return_value=Path("credential-process-macos-arm64")),
        otel_patch,
    ):
        result = tester.execute("--target-platform macos-arm64 --legacy --skip-validation")
    return result, tester


class TestOtelHelperGate:
    def test_swallowed_otel_helper_failure_fails_the_package(self, monkeypatch, tmp_path, mock_config, capsys):
        """The regression: otel-helper build raises, warning printed, package exited 0."""
        profile = _make_monitoring_profile()
        result, tester = _run_package(monkeypatch, tmp_path, mock_config(profile), RuntimeError("PyInstaller exploded"))
        # rich Console writes to sys.stdout (not the cleo IO), so read via capsys.
        output = capsys.readouterr().out
        assert result == 1, (
            "Monitoring is enabled and the otel-helper build failed — packaging must exit non-zero, "
            f"got {result}.\n{output}"
        )
        assert "otel-helper" in output

    def test_otel_helper_returning_none_fails_the_package(self, monkeypatch, tmp_path, mock_config):
        """A None return (silently skipped build) must be treated the same as a raise."""
        profile = _make_monitoring_profile()
        result, tester = _run_package(monkeypatch, tmp_path, mock_config(profile), None)
        assert result == 1, tester.io.fetch_output()

    def test_successful_otel_helper_build_exits_zero(self, monkeypatch, tmp_path, mock_config):
        """Sanity: with both binaries built, the gate must not fire."""
        profile = _make_monitoring_profile()
        result, tester = _run_package(monkeypatch, tmp_path, mock_config(profile), Path("otel-helper-macos-arm64"))
        assert result == 0, tester.io.fetch_output()

    def test_monitoring_disabled_ignores_missing_otel_helper(self, monkeypatch, tmp_path, mock_config):
        """No monitoring → otel-helper is never built; its absence must not fail packaging."""
        profile = _make_monitoring_profile(monitoring_enabled=False, otel_collector_endpoint=None)
        result, tester = _run_package(monkeypatch, tmp_path, mock_config(profile), RuntimeError("must never be called"))
        assert result == 0, tester.io.fetch_output()
