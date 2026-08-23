# ABOUTME: Regression test — the wizard's ALB scheme answer must survive save/reload
# ABOUTME: Guards _save_configuration/_check_existing_deployment against dropping alb_scheme

"""Regression test: monitoring.alb_scheme must round-trip through the profile.

The init wizard (central monitoring mode) asks for the ALB network exposure and
stores it at config["monitoring"]["alb_scheme"], but `_save_configuration`'s
monitoring_config builder copied only vpc_config/custom_domain/hosted_zone_id —
alb_scheme was dropped, and `_check_existing_deployment` never restored it.
deploy.py only passes ALBScheme=internal when profile.monitoring_config carries
"internal", so an admin who selected "Internal (private ...)" silently got an
INTERNET-FACING telemetry ALB.

This is the config-sync.md round-trip class (PRs #436/#619/#624).

Backward compatibility: profiles saved without the key must stay without it —
absence must NOT default to "internal" (deploy already defaults to
internet-facing).

Tests avoid any TTY: save and restore paths are exercised directly, and the
wizard tests mock questionary to capture the ``default=`` offered to the
ALB-scheme select (same patterns as test_init_idc_roundtrip.py).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ruff: noqa: E402
sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_code_with_bedrock.cli.commands.init import InitCommand
from claude_code_with_bedrock.config import Config, Profile

VPC_CONFIG = {
    "create_vpc": False,
    "vpc_id": "vpc-0123456789abcdef0",
    "subnet_ids": ["subnet-aaa", "subnet-bbb"],
}

ALB_PROMPT = "Load balancer network exposure:"


def _central_config_data(alb_scheme: str | None) -> dict:
    """Minimal wizard output for a central-monitoring deployment."""
    monitoring = {
        "enabled": True,
        "mode": "central",
        "vpc_config": dict(VPC_CONFIG),
    }
    if alb_scheme is not None:
        monitoring["alb_scheme"] = alb_scheme
    return {
        "auth_type": "none",
        "sso_enabled": False,
        "credential_storage": "session",
        "aws": {
            "region": "us-east-1",
            "identity_pool_name": "claude-code-auth",
            "stacks": {},
            "allowed_bedrock_regions": ["us-east-1"],
        },
        "monitoring": monitoring,
    }


def _save_and_capture(config_data: dict) -> Profile:
    """Run _save_configuration with Config I/O stubbed out; return the saved Profile."""
    command = InitCommand()
    fake_config = Config()
    saved = {}
    with (
        patch.object(Config, "load", return_value=fake_config),
        patch.object(fake_config, "get_profile", return_value=None),
        patch.object(fake_config, "add_profile", side_effect=lambda p: saved.update(profile=p)),
        patch.object(fake_config, "set_active_profile"),
        patch.object(fake_config, "save"),
    ):
        command._save_configuration(config_data, "test")
    return saved["profile"]


def _rebuild_config(profile: Profile) -> dict:
    """Run _check_existing_deployment with AWS interaction stubbed out."""
    command = InitCommand()
    fake_config = Config()
    with (
        patch.object(Config, "load", return_value=fake_config),
        patch.object(fake_config, "get_profile", return_value=profile),
        # Avoid any AWS calls; pretend the stack check could not run.
        patch.object(InitCommand, "_stack_exists", side_effect=Exception("no creds")),
    ):
        return command._check_existing_deployment("test")


def test_save_persists_internal_alb_scheme():
    """monitoring.alb_scheme='internal' must land in the profile's monitoring_config."""
    profile = _save_and_capture(_central_config_data("internal"))

    assert profile.monitoring_config.get("alb_scheme") == "internal", (
        "_save_configuration dropped alb_scheme — deploy.py reads "
        "monitoring_config['alb_scheme'] and would deploy an internet-facing ALB"
    )


def test_save_persists_internet_facing_alb_scheme():
    """An explicit internet-facing answer is persisted too (not just 'internal')."""
    profile = _save_and_capture(_central_config_data("internet-facing"))

    assert profile.monitoring_config.get("alb_scheme") == "internet-facing"


def test_save_without_alb_scheme_stays_absent():
    """Sidecar/legacy wizard output without the key must not invent a value."""
    config_data = _central_config_data(None)
    config_data["monitoring"]["mode"] = "sidecar"
    del config_data["monitoring"]["vpc_config"]
    profile = _save_and_capture(config_data)

    assert "alb_scheme" not in profile.monitoring_config, (
        "absent alb_scheme must stay absent — deploy defaults to internet-facing"
    )


def test_rerun_restores_saved_alb_scheme():
    """_check_existing_deployment must carry alb_scheme back into the wizard config."""
    profile = _save_and_capture(_central_config_data("internal"))
    rebuilt = _rebuild_config(profile)

    assert rebuilt["monitoring"].get("alb_scheme") == "internal", (
        "re-running init dropped alb_scheme — the wizard would default back to "
        "internet-facing and silently rewrite the profile"
    )


def test_full_roundtrip_save_reload_resave():
    """save -> reload -> re-save (accept every existing answer) must not lose the scheme."""
    profile = _save_and_capture(_central_config_data("internal"))
    rebuilt = _rebuild_config(profile)
    resaved = _save_and_capture(rebuilt)

    assert resaved.monitoring_config.get("alb_scheme") == "internal"


def test_rerun_of_old_profile_does_not_gain_a_scheme():
    """Old profiles (saved before alb_scheme existed) must behave exactly as today."""
    old_profile = Profile(
        name="test",
        provider_domain="none",
        client_id="none",
        identity_pool_name="claude-code-auth",
        credential_storage="session",
        aws_region="us-east-1",
        monitoring_enabled=True,
        monitoring_mode="central",
        monitoring_config=dict(VPC_CONFIG),  # flattened, no alb_scheme
    )
    rebuilt = _rebuild_config(old_profile)

    # The restore path may carry None but must never manufacture "internal".
    assert rebuilt["monitoring"].get("alb_scheme") is None

    # And a re-save of that rebuilt config must leave the key absent.
    resaved = _save_and_capture(rebuilt)
    assert "alb_scheme" not in resaved.monitoring_config


class _AlbPromptReached(Exception):
    """Raised by the fake select once the ALB-scheme prompt has been captured."""


def _capture_alb_select_default(existing_config: dict) -> str | None:
    """Drive _gather_configuration to the ALB-scheme select; return its default=.

    Uses auth_type="none" so the wizard skips the OIDC/IDC sections, mocks all
    questionary prompts to accept their offered defaults, and stubs out AWS
    helpers (_configure_vpc, get_current_region). The fake select aborts the
    wizard as soon as the ALB prompt appears.
    """
    command = InitCommand()
    captured = {}

    def fake_select(*args, **kwargs):
        prompt = MagicMock()
        message = args[0] if args else kwargs.get("message", "")
        if message == ALB_PROMPT:
            captured["default"] = kwargs.get("default")
            prompt.ask.side_effect = _AlbPromptReached()
        else:
            prompt.ask.return_value = kwargs.get("default")
        return prompt

    def fake_text(*args, **kwargs):
        prompt = MagicMock()
        prompt.ask.return_value = kwargs.get("default", "")
        return prompt

    def fake_confirm(*args, **kwargs):
        prompt = MagicMock()
        prompt.ask.return_value = kwargs.get("default", True)
        return prompt

    with (
        patch("claude_code_with_bedrock.cli.commands.init.questionary.select", side_effect=fake_select),
        patch("claude_code_with_bedrock.cli.commands.init.questionary.text", side_effect=fake_text),
        patch("claude_code_with_bedrock.cli.commands.init.questionary.confirm", side_effect=fake_confirm),
        patch("claude_code_with_bedrock.cli.commands.init.get_current_region", return_value="us-east-1"),
        patch.object(InitCommand, "_configure_vpc", return_value=dict(VPC_CONFIG)),
        pytest.raises(_AlbPromptReached),
    ):
        command._gather_configuration(MagicMock(), existing_config, "test")

    assert "default" in captured, "the ALB-scheme select was never shown"
    return captured["default"]


def test_wizard_select_defaults_to_saved_scheme():
    """The ALB-scheme select must be offered the restored value as its default."""
    profile = _save_and_capture(_central_config_data("internal"))
    rebuilt = _rebuild_config(profile)

    assert _capture_alb_select_default(rebuilt) == "internal", (
        "the ALB-scheme select must pre-select the saved value, not reset to internet-facing"
    )


def test_wizard_select_defaults_to_internet_facing_for_old_profile():
    """A restored old profile (alb_scheme=None) must default to internet-facing."""
    old_profile = Profile(
        name="test",
        provider_domain="none",
        client_id="none",
        identity_pool_name="claude-code-auth",
        credential_storage="session",
        aws_region="us-east-1",
        auth_type="none",  # keep the wizard walk off the OIDC/IDC prompt paths
        sso_enabled=False,
        monitoring_enabled=True,
        monitoring_mode="central",
        monitoring_config=dict(VPC_CONFIG),
    )
    rebuilt = _rebuild_config(old_profile)

    assert _capture_alb_select_default(rebuilt) == "internet-facing", (
        "questionary.select must receive a valid choice value (not None) for old profiles"
    )
