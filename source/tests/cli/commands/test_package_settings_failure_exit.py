# ABOUTME: Regression tests for Claude settings failure propagation in ccwb package
# ABOUTME: Settings-generation failures must exit 1, not print a warning and exit 0

"""_create_claude_settings failures used to be swallowed.

The whole method body sat in one try/except whose handler printed only
"[yellow]Warning: Could not create Claude Code settings" — and handle() ignored
the outcome — so any exception left the package without
claude-settings/settings.json while packaging still exited 0. Similarly, the
monitoring-enabled-but-no-endpoint path printed a red ERROR but wrote a
settings.json silently lacking ALL telemetry configuration, also exiting 0.

These tests pin the contract: _create_claude_settings returns False for both
failure modes, and handle() propagates that into a non-zero exit code.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cleo.testers.command_tester import CommandTester

from claude_code_with_bedrock.cli.commands.package import PackageCommand
from claude_code_with_bedrock.config import Config, Profile


def _make_profile(**overrides) -> Profile:
    kwargs = {
        "name": "test",
        "provider_domain": "test.okta.com",
        "client_id": "test-client-id",
        "credential_storage": "keyring",
        "aws_region": "us-east-1",
        "identity_pool_name": "test-pool",
        "monitoring_enabled": False,
        "enable_codebuild": False,
        "cowork_3p_enabled": False,
    }
    kwargs.update(overrides)
    return Profile(**kwargs)


class TestCreateClaudeSettingsReturnValue:
    def test_returns_true_on_success(self, tmp_path):
        command = PackageCommand()
        assert command._create_claude_settings(tmp_path, _make_profile()) is True
        assert (tmp_path / "claude-settings" / "settings.json").exists()

    def test_returns_false_on_exception(self, tmp_path):
        """The regression: an exception printed a warning and the caller saw nothing."""
        # Occupy the claude-settings path with a FILE so mkdir(exist_ok=True) raises.
        (tmp_path / "claude-settings").write_text("collision", encoding="utf-8")
        command = PackageCommand()
        result = command._create_claude_settings(tmp_path, _make_profile())
        assert result is False, "_create_claude_settings must report failure when settings.json cannot be created"

    def test_returns_false_when_monitoring_has_no_endpoint(self, tmp_path):
        """Monitoring enabled + no resolvable endpoint → red ERROR used to exit 0.

        The settings file is still written (Bedrock env only), but it silently
        lacks all telemetry configuration — that must surface as a failure."""
        # No otel_collector_endpoint, no stacks to query, empty pool name so the
        # CloudFormation fallback has nothing to probe.
        profile = _make_profile(monitoring_enabled=True, monitoring_mode="central", identity_pool_name="")

        command = PackageCommand()
        mock_text = MagicMock()
        mock_text.return_value.ask.return_value = ""  # admin skips the manual-endpoint prompt
        with patch("questionary.text", mock_text):
            result = command._create_claude_settings(tmp_path, profile)

        assert result is False

        # Backward-compat: the file is still written, just without telemetry.
        settings = json.loads((tmp_path / "claude-settings" / "settings.json").read_text(encoding="utf-8"))
        assert settings["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in settings["env"]

    def test_returns_true_for_sidecar_monitoring(self, tmp_path):
        """Sidecar mode always resolves localhost:4318 — never a failure."""
        profile = _make_profile(monitoring_enabled=True, monitoring_mode="sidecar")
        command = PackageCommand()
        assert command._create_claude_settings(tmp_path, profile) is True


@pytest.fixture
def mock_config():
    profile = _make_profile()
    config = MagicMock(spec=Config)
    config.get_profile.return_value = profile
    config.active_profile = "test"
    return config


class TestHandlePropagatesSettingsFailure:
    def _run(self, monkeypatch, tmp_path, mock_config, settings_ok: bool):
        monkeypatch.chdir(tmp_path)
        command = PackageCommand()
        tester = CommandTester(command)
        with (
            patch("claude_code_with_bedrock.config.Config.load", return_value=mock_config),
            patch("claude_code_with_bedrock.cli.commands.package._is_interactive", return_value=False),
            patch.object(
                PackageCommand,
                "_resolve_federation",
                return_value=("cognito", "us-east-1:fake-pool-id", None),
            ),
            patch.object(PackageCommand, "_build_executable", return_value=Path("credential-process-macos-arm64")),
            patch.object(PackageCommand, "_create_claude_settings", return_value=settings_ok),
        ):
            result = tester.execute("--target-platform macos-arm64 --legacy --skip-validation")
        return result, tester

    def test_settings_failure_exits_nonzero(self, monkeypatch, tmp_path, mock_config):
        result, tester = self._run(monkeypatch, tmp_path, mock_config, settings_ok=False)
        assert result == 1, (
            f"Packaging must exit non-zero when Claude settings generation fails.\n{tester.io.fetch_output()}"
        )

    def test_settings_success_exits_zero(self, monkeypatch, tmp_path, mock_config):
        result, tester = self._run(monkeypatch, tmp_path, mock_config, settings_ok=True)
        assert result == 0, tester.io.fetch_output()
