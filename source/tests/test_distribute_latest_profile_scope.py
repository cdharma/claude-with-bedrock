# ABOUTME: Regression tests - `distribute --latest` must select the target profile's newest build
# ABOUTME: Previously it picked the newest timestamp ACROSS profiles, cross-publishing another profile's package

"""Regression tests for ``ccwb distribute --latest`` profile scoping (finding x2).

``--latest`` used to select the newest timestamp across ALL profile directories
under dist/ (deliberately discarding the profile name), while the S3 bucket,
CodeBuild project, and SSM parameter came from the --profile/active profile
resolved afterwards. Profile A's build could therefore be silently uploaded and
published as profile B's latest, with only an informational "Auto-selected
latest build" line.

These tests drive ``handle()`` end-to-end (with the actual distribution step
stubbed out) and assert:
- ``--latest`` picks the target profile's newest build even when another
  profile has a newer one, and
- ``--latest`` fails (exit 1) instead of silently borrowing another profile's
  build when the target profile has no builds at all.
"""

from types import SimpleNamespace

import pytest

from claude_code_with_bedrock.cli.commands.distribute import DistributeCommand
from claude_code_with_bedrock.config import Profile


def _make_build(dist_dir, profile_name, timestamp):
    build = dist_dir / profile_name / timestamp
    build.mkdir(parents=True)
    (build / "config.json").write_text("{}")
    (build / "install.sh").write_text("#!/bin/bash")
    return build


def _make_cmd(dist_dir, profile_option=None):
    cmd = DistributeCommand.__new__(DistributeCommand)
    cmd.option = {
        "package-path": str(dist_dir),
        "build-profile": None,
        "timestamp": None,
        "latest": True,
        "profile": profile_option,
        "get-latest": False,
        "expires-hours": "48",
        "allowed-ips": None,
        "show-qr": False,
        "per-os": False,
    }.get
    return cmd


@pytest.fixture
def target_profile():
    """Distribution disabled → handle() routes straight to _create_distribution (stubbed)."""
    return Profile(
        name="profile-a",
        provider_domain="example.okta.com",
        client_id="client-123",
        credential_storage="keyring",
        aws_region="us-east-1",
        identity_pool_name="pool-a",
        enable_distribution=False,
    )


@pytest.fixture
def fake_config(target_profile, monkeypatch):
    """Stub Config.load() inside the distribute module."""
    config = SimpleNamespace(
        active_profile="profile-a",
        get_profile=lambda name: target_profile if name == "profile-a" else None,
    )
    monkeypatch.setattr(
        "claude_code_with_bedrock.cli.commands.distribute.Config",
        SimpleNamespace(load=lambda: config),
    )
    return config


class TestLatestScopedToTargetProfile:
    def test_latest_picks_target_profile_build_not_newer_foreign_build(self, tmp_path, fake_config, target_profile):
        """Pre-fix, profile-b's newer build was selected and uploaded as profile-a's latest."""
        dist_dir = tmp_path / "dist"
        _make_build(dist_dir, "profile-a", "2026-01-01-000000")
        _make_build(dist_dir, "profile-b", "2026-02-02-000000")  # newer, different profile

        cmd = _make_cmd(dist_dir)
        selected = []
        cmd._create_distribution = lambda profile, console, package_path: (selected.append(package_path), 0)[1]

        result = cmd.handle()

        assert result == 0
        assert len(selected) == 1
        assert selected[0].parent.name == "profile-a", (
            "--latest must never select another profile's build for this profile's bucket/SSM parameter"
        )
        assert selected[0].name == "2026-01-01-000000"

    def test_latest_picks_newest_build_within_target_profile(self, tmp_path, fake_config, target_profile):
        dist_dir = tmp_path / "dist"
        _make_build(dist_dir, "profile-a", "2026-01-01-000000")
        _make_build(dist_dir, "profile-a", "2026-03-03-000000")

        cmd = _make_cmd(dist_dir)
        selected = []
        cmd._create_distribution = lambda profile, console, package_path: (selected.append(package_path), 0)[1]

        result = cmd.handle()

        assert result == 0
        assert selected[0].name == "2026-03-03-000000"

    def test_latest_fails_when_target_profile_has_no_builds(self, tmp_path, fake_config, target_profile):
        """Pre-fix, the foreign build was silently used and the command exited 0."""
        dist_dir = tmp_path / "dist"
        _make_build(dist_dir, "profile-b", "2026-02-02-000000")  # only a foreign profile has builds

        cmd = _make_cmd(dist_dir)
        selected = []
        cmd._create_distribution = lambda profile, console, package_path: (selected.append(package_path), 0)[1]

        result = cmd.handle()

        assert result == 1, "--latest with no builds for the target profile must fail, not borrow another profile's"
        assert selected == [], "no distribution must be attempted with a foreign build"

    def test_latest_respects_explicit_profile_option(self, tmp_path, target_profile, monkeypatch):
        """--profile profile-a scopes --latest the same way the active profile does."""
        dist_dir = tmp_path / "dist"
        _make_build(dist_dir, "profile-a", "2026-01-01-000000")
        _make_build(dist_dir, "profile-b", "2026-02-02-000000")

        config = SimpleNamespace(
            active_profile="profile-b",
            get_profile=lambda name: target_profile if name == "profile-a" else None,
        )
        monkeypatch.setattr(
            "claude_code_with_bedrock.cli.commands.distribute.Config",
            SimpleNamespace(load=lambda: config),
        )

        cmd = _make_cmd(dist_dir, profile_option="profile-a")
        selected = []
        cmd._create_distribution = lambda profile, console, package_path: (selected.append(package_path), 0)[1]

        result = cmd.handle()

        assert result == 0
        assert selected[0].parent.name == "profile-a"
