# ABOUTME: Regression tests — re-running init must round-trip redirect_port, settings_target
# ABOUTME: and the token quota default, and IDC start URL/SSO region must be format-validated

"""Regression tests for four init-wizard defects (config-sync.md round-trip class).

[i0] redirect_port was saved by _save_configuration but never restored by
     _check_existing_deployment. Re-running init and accepting the "custom
     OAuth callback port?" No-default silently rewrote redirect_port to None;
     repackaged binaries then fell back to 8400 while the IdP app only had the
     custom port registered → redirect_uri mismatch for every end user.

[i1] The IDC start URL / SSO region prompts validated only non-emptiness, so
     garbage was accepted and only failed later on end-user machines via the
     packaged AWS config (sso_start_url / sso_region). The region auto-suggest
     regex also missed GovCloud regions.

[i2] The token-based monthly limit prompt defaulted from a phantom
     "monthly_limit_millions" key that is never persisted; re-running init
     always showed 225 and pressing Enter rewrote the saved limit to 225M.

[i3] settings_target was saved but never restored, so re-running init
     pre-selected "User scope" and accepting the default downgraded a managed
     profile (dropping org-enforced managed-settings on the next package).

Tests avoid any TTY: the restore path is exercised directly and the wizard is
driven with questionary mocks that answer prompts by their message text.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ruff: noqa: E402
sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_code_with_bedrock.cli.commands.init import (
    InitCommand,
    validate_idc_start_url,
    validate_sso_region,
)
from claude_code_with_bedrock.config import Config, Profile


def _make_oidc_profile(**overrides) -> Profile:
    """A profile as _save_configuration writes it for an Okta OIDC deployment."""
    fields = {
        "name": "test",
        "provider_domain": "example.okta.com",
        "client_id": "0oa1234567890abc",
        "identity_pool_name": "claude-code-auth",
        "credential_storage": "session",
        "aws_region": "us-east-1",
        "auth_type": "oidc",
        "sso_enabled": True,
        "provider_type": "okta",
        "federation_type": "direct",
    }
    fields.update(overrides)
    return Profile(**fields)


def _make_idc_profile(**overrides) -> Profile:
    """A profile as _save_configuration writes it for an IDC deployment."""
    fields = {
        "name": "test",
        "provider_domain": "none",
        "client_id": "none",
        "identity_pool_name": "claude-code-auth",
        "credential_storage": "session",
        "aws_region": "us-east-1",
        "auth_type": "idc",
        "sso_enabled": False,
        "idc_start_url": "https://acme.awsapps.com/start",
        "idc_account_id": "123456789012",
        "idc_permission_set_name": "BedrockDeveloperAccess",
        "sso_region": "us-east-1",
    }
    fields.update(overrides)
    return Profile(**fields)


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


class _WizardStopped(Exception):
    """Raised by the driver to halt the wizard once the prompt under test ran."""


class _WizardDriver:
    """Answers questionary prompts by message text; records every call.

    Unhandled prompts answer with their offered default. ``CANCEL`` simulates
    Ctrl+C (``.ask()`` -> None) for prompts with a proper cancel path; ``STOP``
    aborts the wizard outright after recording the prompt (for prompts whose
    cancel path is not under test here).
    """

    CANCEL = object()
    STOP = object()

    def __init__(self, answers: dict | None = None):
        self.answers = answers or {}
        self.calls = []  # (kind, message, kwargs)

    def _handle(self, kind, args, kwargs):
        message = args[0] if args else kwargs.get("message", "")
        self.calls.append((kind, message, kwargs))
        prompt = MagicMock()
        if message in self.answers:
            answer = self.answers[message]
            answer = answer(kwargs) if callable(answer) else answer
        else:
            answer = kwargs.get("default")
        if answer is self.STOP:
            raise _WizardStopped(message)
        prompt.ask.return_value = None if answer is self.CANCEL else answer
        return prompt

    def kwargs_for(self, message):
        for _, msg, kwargs in self.calls:
            if msg == message:
                return kwargs
        raise AssertionError(f"prompt {message!r} was never shown; saw {[m for _, m, _ in self.calls]}")

    def patches(self):
        base = "claude_code_with_bedrock.cli.commands.init.questionary"
        return [
            patch(f"{base}.select", side_effect=lambda *a, **k: self._handle("select", a, k)),
            patch(f"{base}.text", side_effect=lambda *a, **k: self._handle("text", a, k)),
            patch(f"{base}.confirm", side_effect=lambda *a, **k: self._handle("confirm", a, k)),
            patch(f"{base}.password", side_effect=lambda *a, **k: self._handle("password", a, k)),
        ]


def _run_wizard(existing_config, driver: _WizardDriver, progress=None):
    """Drive _gather_configuration without a TTY, environment or AWS access."""
    command = InitCommand()
    if progress is None:
        progress = MagicMock()
    with (
        patch("claude_code_with_bedrock.cli.utils.helpers.is_wsl", return_value=False),
        patch("claude_code_with_bedrock.cli.utils.helpers.is_keyring_available", return_value=False),
        patch("claude_code_with_bedrock.cli.commands.init.get_current_region", return_value="us-east-1"),
    ):
        patches = driver.patches()
        for p in patches:
            p.start()
        try:
            return command._gather_configuration(progress, existing_config, "test")
        except _WizardStopped:
            return None
        finally:
            for p in patches:
                p.stop()


PORT_CONFIRM = "Use a custom OAuth callback port? (default: 8400)"
PORT_TEXT = "Enter OAuth callback port:"
FEDERATION_SELECT = "Federation type:"


# ---------------------------------------------------------------------------
# [i0] redirect_port round-trip
# ---------------------------------------------------------------------------


def test_rerun_restores_redirect_port():
    """_check_existing_deployment must restore the saved custom callback port."""
    rebuilt = _rebuild_config(_make_oidc_profile(redirect_port=9000))
    assert rebuilt.get("redirect_port") == 9000


def test_redirect_port_confirm_defaults_yes_and_prefills_saved_port():
    """With a saved custom port, the confirm must default Yes and the text
    prompt must pre-fill the saved port (not 8400)."""
    existing_config = _rebuild_config(_make_oidc_profile(redirect_port=9000))
    driver = _WizardDriver(
        answers={
            PORT_CONFIRM: True,
            FEDERATION_SELECT: _WizardDriver.CANCEL,  # stop right after the port block
        }
    )

    result = _run_wizard(existing_config, driver)

    assert result is None  # cancelled at the federation select
    assert driver.kwargs_for(PORT_CONFIRM).get("default") is True
    assert driver.kwargs_for(PORT_TEXT).get("default") == "9000"


def test_redirect_port_preserved_when_confirm_declined(capsys):
    """Answering No must keep the saved custom port instead of silently
    resetting it to None (→ 8400 in the packaged binary)."""
    existing_config = _rebuild_config(_make_oidc_profile(redirect_port=9000))
    driver = _WizardDriver(
        answers={
            PORT_CONFIRM: False,
            FEDERATION_SELECT: _WizardDriver.CANCEL,
        }
    )

    _run_wizard(existing_config, driver)

    out = capsys.readouterr().out
    assert "9000" in out, "wizard did not announce it kept the saved custom port"
    assert "Keeping previously configured OAuth callback port" in out
    # The port text prompt must not have been shown on decline.
    assert all(msg != PORT_TEXT for _, msg, _ in driver.calls)


def test_redirect_port_confirm_defaults_no_for_fresh_profiles():
    """Backward compat: without a saved custom port the confirm still defaults No."""
    existing_config = _rebuild_config(_make_oidc_profile())  # no redirect_port
    assert "redirect_port" not in existing_config
    driver = _WizardDriver(answers={FEDERATION_SELECT: _WizardDriver.CANCEL})

    _run_wizard(existing_config, driver)

    assert driver.kwargs_for(PORT_CONFIRM).get("default") is False


# ---------------------------------------------------------------------------
# [i1] IDC start URL / SSO region validation
# ---------------------------------------------------------------------------


def test_idc_start_url_validator():
    assert validate_idc_start_url("https://acme.awsapps.com/start") is True
    assert validate_idc_start_url("https://start.us-gov-home.awsapps.com/directory/acme") is True
    for garbage in (
        "",
        "   ",
        "garbage",
        "acme.awsapps.com/start",
        "http://acme.awsapps.com/start",
        "https://",
        "https://nodots",
    ):
        assert validate_idc_start_url(garbage) is not True, f"{garbage!r} must be rejected"


def test_sso_region_validator():
    for region in ("us-east-1", "eu-west-2", "ap-southeast-2", "us-gov-west-1", "us-gov-east-1", "il-central-1"):
        assert validate_sso_region(region) is True, f"{region!r} must be accepted"
    for garbage in ("", "   ", "garbage", "us-east", "us_east_1", "US-EAST-1", "https://us-east-1"):
        assert validate_sso_region(garbage) is not True, f"{garbage!r} must be rejected"


def test_idc_prompts_wired_to_format_validators():
    """The wizard prompts must reject garbage, not just empty strings."""
    existing_config = _rebuild_config(_make_idc_profile())
    driver = _WizardDriver(
        answers={"Enter your SSO region (where Identity Center is configured):": _WizardDriver.CANCEL}
    )

    _run_wizard(existing_config, driver)

    url_validate = driver.kwargs_for("Enter your IAM Identity Center start URL:").get("validate")
    assert url_validate is not None
    assert url_validate("https://acme.awsapps.com/start") is True
    assert url_validate("garbage") is not True, "start URL prompt accepts arbitrary non-empty strings"

    region_validate = driver.kwargs_for("Enter your SSO region (where Identity Center is configured):").get("validate")
    assert region_validate is not None
    assert region_validate("us-gov-west-1") is True
    assert region_validate("garbage") is not True, "SSO region prompt accepts arbitrary non-empty strings"


def test_sso_region_suggestion_handles_govcloud():
    """The auto-suggest regex must recognise GovCloud regions in the start URL."""
    progress = MagicMock()
    progress.get_saved_data.return_value = {}
    progress.get_last_step.return_value = None
    driver = _WizardDriver(
        answers={
            "Select authentication method:": "idc",
            "Enter your IAM Identity Center start URL:": (
                "https://start.us-gov-west-1.us-gov-home.awsapps.com/directory/acme"
            ),
            "Enter your SSO region (where Identity Center is configured):": _WizardDriver.CANCEL,
        }
    )

    _run_wizard(None, driver, progress=progress)

    region_kwargs = driver.kwargs_for("Enter your SSO region (where Identity Center is configured):")
    assert region_kwargs.get("default") == "us-gov-west-1"


# ---------------------------------------------------------------------------
# [i2] token monthly limit prompt default
# ---------------------------------------------------------------------------


def test_monthly_token_limit_prompt_prefills_saved_limit():
    """The prompt default must come from the saved raw monthly_limit, not 225."""
    profile = _make_idc_profile(
        monitoring_enabled=True,
        monitoring_mode="sidecar",
        quota_monitoring_enabled=True,
        quota_limit_type="token",
        monthly_token_limit=150_000_000,
    )
    existing_config = _rebuild_config(profile)
    assert existing_config["quota"]["monthly_limit"] == 150_000_000

    limit_prompt = "Monthly token limit per user (in millions):"
    driver = _WizardDriver(answers={limit_prompt: _WizardDriver.STOP})

    _run_wizard(existing_config, driver)

    assert driver.kwargs_for(limit_prompt).get("default") == "150"


def test_monthly_token_limit_prompt_falls_back_to_225_when_unset():
    """Fresh installs (and cost-mode profiles with monthly_limit 0) keep 225 —
    a 0 default would fail the > 0 validation and trap the user."""
    profile = _make_idc_profile(
        monitoring_enabled=True,
        monitoring_mode="sidecar",
        quota_monitoring_enabled=True,
        quota_limit_type="token",
        monthly_token_limit=0,  # cost-mode profiles persist 0 token limits
    )
    existing_config = _rebuild_config(profile)

    limit_prompt = "Monthly token limit per user (in millions):"
    driver = _WizardDriver(answers={limit_prompt: _WizardDriver.STOP})

    _run_wizard(existing_config, driver)

    assert driver.kwargs_for(limit_prompt).get("default") == "225"


# ---------------------------------------------------------------------------
# [i3] settings_target round-trip
# ---------------------------------------------------------------------------


def test_rerun_restores_settings_target_managed():
    """A managed profile must not be silently downgraded to user scope."""
    rebuilt = _rebuild_config(_make_oidc_profile(settings_target="managed"))
    assert rebuilt.get("settings_target") == "managed"


def test_rerun_defaults_settings_target_to_user():
    """Backward compat: old/default profiles round-trip as user scope."""
    rebuilt = _rebuild_config(_make_oidc_profile())
    assert rebuilt.get("settings_target") == "user"
