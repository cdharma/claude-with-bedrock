# ABOUTME: Regression tests - distribute must not report success (exit 0) when a platform upload fails
# ABOUTME: Also guards against deleting packages/*/latest.zip in S3 before the replacement uploads succeed

"""Regression tests for partial-upload failure handling in ``ccwb distribute``.

Defects (finding x0):

1. ``_upload_landing_page_packages`` deleted every ``packages/*/latest.zip``
   BEFORE uploading anything, swallowed per-platform upload failures with
   ``continue``, and then — as long as at least one platform uploaded — printed
   EVERY available platform under "Uploaded platforms:" and returned 0. A
   failed platform was therefore left deleted in S3 (404 for users) while the
   summary claimed it uploaded and the command exited 0.

2. ``_distribute_per_os`` returned 0 whenever at least one platform succeeded,
   even if others failed — violating the exit-code contract
   (see .claude/rules/pr-standards.md "Exit Code Contract").

These tests drive both paths with a fake S3 client that fails selected keys and
assert: non-zero exit on any failure, an accurate "Uploaded platforms" list,
and that latest.zip objects are only ever deleted AFTER successful uploads
(and only for platforms that are not part of the current build).
"""

import io
import zipfile

import pytest
from rich.console import Console

from claude_code_with_bedrock.cli.commands.distribute import DistributeCommand
from claude_code_with_bedrock.config import Profile


class FakeS3:
    """Records upload/delete events in order; fails uploads whose key matches a token."""

    def __init__(self, fail_key_tokens=()):
        self.fail_key_tokens = tuple(fail_key_tokens)
        self.events = []  # ordered ("upload"|"delete", key) tuples
        self.zip_contents = {}  # key -> list of archive member names

    def upload_file(self, Filename=None, Bucket=None, Key=None, ExtraArgs=None, Config=None, Callback=None):
        if any(token in Key for token in self.fail_key_tokens):
            # ValueError: _upload_file_with_retry re-raises it immediately (no retry sleep)
            raise ValueError(f"simulated upload failure for {Key}")
        if Filename and str(Filename).endswith(".zip"):
            with zipfile.ZipFile(Filename) as zf:
                self.zip_contents[Key] = zf.namelist()
        self.events.append(("upload", Key))

    def delete_object(self, Bucket=None, Key=None):
        self.events.append(("delete", Key))

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):
        return f"https://example.com/{Params['Key']}"

    @property
    def uploads(self):
        return [key for event, key in self.events if event == "upload"]

    @property
    def deletes(self):
        return [key for event, key in self.events if event == "delete"]


@pytest.fixture
def cmd():
    """DistributeCommand instance without invoking CLI machinery."""
    command = DistributeCommand.__new__(DistributeCommand)
    command.option = {
        "expires-hours": "24",
        "per-os": False,
        "allowed-ips": None,
        "show-qr": False,
    }.get
    return command


@pytest.fixture
def profile():
    return Profile(
        name="test",
        provider_domain="example.okta.com",
        client_id="client-123",
        credential_storage="keyring",
        aws_region="us-east-1",
        identity_pool_name="test-pool",
        enable_distribution=True,
        distribution_type="landing-page",
    )


@pytest.fixture
def stack_outputs(monkeypatch):
    monkeypatch.setattr(
        "claude_code_with_bedrock.cli.commands.distribute.get_stack_outputs",
        lambda *args, **kwargs: {
            "DistributionBucket": "dist-bucket",
            "DistributionURL": "https://landing.example.com",
        },
    )


def _full_package(tmp_path):
    """Package dir with binaries for every landing family (windows, linux, mac)."""
    pkg = tmp_path / "dist" / "my-profile" / "2026-06-01-120000"
    pkg.mkdir(parents=True)
    for name in (
        "credential-process-windows.exe",
        "otel-helper-windows.exe",
        "credential-process-linux-x64",
        "otel-helper-linux-x64",
        "credential-process-macos-arm64",
        "otel-helper-macos-arm64",
        "install.sh",
        "install.bat",
        "config.json",
        "README.md",
    ):
        (pkg / name).write_text("stub")
    return pkg


def _windows_only_package(tmp_path):
    pkg = tmp_path / "dist" / "my-profile" / "2026-06-01-120000"
    pkg.mkdir(parents=True)
    for name in (
        "credential-process-windows.exe",
        "otel-helper-windows.exe",
        "install.bat",
        "config.json",
    ):
        (pkg / name).write_text("stub")
    return pkg


def _run_landing(cmd, profile, pkg, fake_s3, monkeypatch, width=200):
    monkeypatch.setattr(DistributeCommand, "_s3_client", staticmethod(lambda region: fake_s3))
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=width)
    result = cmd._upload_landing_page_packages(profile, console, pkg)
    return result, buffer.getvalue()


class TestLandingPagePartialFailure:
    """One platform failing to upload must be visible in the exit code and summary."""

    def test_exit_code_nonzero_when_a_platform_fails(self, cmd, profile, stack_outputs, tmp_path, monkeypatch):
        pkg = _full_package(tmp_path)
        fake_s3 = FakeS3(fail_key_tokens=("packages/linux/",))

        result, _ = _run_landing(cmd, profile, pkg, fake_s3, monkeypatch)

        assert result != 0, "a failed platform upload must not exit 0 (exit-code contract)"

    def test_summary_lists_only_actually_uploaded_platforms(self, cmd, profile, stack_outputs, tmp_path, monkeypatch):
        pkg = _full_package(tmp_path)
        fake_s3 = FakeS3(fail_key_tokens=("packages/linux/",))

        _, output = _run_landing(cmd, profile, pkg, fake_s3, monkeypatch)

        assert "Uploaded platforms:" in output
        uploaded_section = output.split("Uploaded platforms:")[1]
        if "Failed platforms" in uploaded_section:
            uploaded_section = uploaded_section.split("Failed platforms")[0]
        assert "windows" in uploaded_section
        assert "mac" in uploaded_section
        assert "linux" not in uploaded_section, "the failed platform must not be reported as uploaded"

    def test_failed_platform_called_out(self, cmd, profile, stack_outputs, tmp_path, monkeypatch):
        pkg = _full_package(tmp_path)
        fake_s3 = FakeS3(fail_key_tokens=("packages/linux/",))

        _, output = _run_landing(cmd, profile, pkg, fake_s3, monkeypatch)

        assert "Failed platforms" in output
        failed_section = output.split("Failed platforms")[1]
        assert "linux" in failed_section

    def test_failed_platform_latest_zip_never_deleted(self, cmd, profile, stack_outputs, tmp_path, monkeypatch):
        """Pre-fix, packages/linux/latest.zip was deleted up front and then the
        upload failed — leaving users a 404. The previous object must survive."""
        pkg = _full_package(tmp_path)
        fake_s3 = FakeS3(fail_key_tokens=("packages/linux/",))

        _run_landing(cmd, profile, pkg, fake_s3, monkeypatch)

        assert "packages/linux/latest.zip" not in fake_s3.deletes
        # All four platforms are part of this build, so nothing is stale:
        assert fake_s3.deletes == []

    def test_all_uploads_failing_returns_nonzero(self, cmd, profile, stack_outputs, tmp_path, monkeypatch):
        pkg = _full_package(tmp_path)
        fake_s3 = FakeS3(fail_key_tokens=("packages/",))

        result, output = _run_landing(cmd, profile, pkg, fake_s3, monkeypatch)

        assert result != 0
        assert "Uploaded platforms:" not in output
        assert fake_s3.deletes == [], "a fully failed run must leave the bucket untouched"


class TestLandingPageStaleCleanup:
    """Stale platforms are still cleaned up — but only after successful uploads."""

    def test_stale_platforms_deleted_only_after_uploads(self, cmd, profile, stack_outputs, tmp_path, monkeypatch):
        pkg = _windows_only_package(tmp_path)
        fake_s3 = FakeS3()

        result, _ = _run_landing(cmd, profile, pkg, fake_s3, monkeypatch)

        assert result == 0
        assert set(fake_s3.uploads) == {"packages/windows/latest.zip", "packages/all-platforms/latest.zip"}
        # linux/mac are not part of this build → stale, cleaned up
        assert set(fake_s3.deletes) == {"packages/linux/latest.zip", "packages/mac/latest.zip"}
        # ...and every delete happens after the last upload (never before)
        upload_positions = [i for i, (event, _) in enumerate(fake_s3.events) if event == "upload"]
        delete_positions = [i for i, (event, _) in enumerate(fake_s3.events) if event == "delete"]
        assert min(delete_positions) > max(upload_positions), "latest.zip must never be deleted before uploading"

    def test_full_success_exits_zero_and_lists_all(self, cmd, profile, stack_outputs, tmp_path, monkeypatch):
        pkg = _full_package(tmp_path)
        fake_s3 = FakeS3()

        result, output = _run_landing(cmd, profile, pkg, fake_s3, monkeypatch)

        assert result == 0
        uploaded_section = output.split("Uploaded platforms:")[1]
        for platform in ("windows", "linux", "mac", "all-platforms"):
            assert platform in uploaded_section


class TestPerOsPartialFailure:
    """_distribute_per_os must exit non-zero when any platform upload fails."""

    def _package(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        for name in (
            "credential-process-windows.exe",
            "otel-helper-windows.exe",
            "install.bat",
            "credential-process-linux-x64",
            "otel-helper-linux-x64",
            "install.sh",
            "config.json",
        ):
            (pkg / name).write_text("stub")
        return pkg

    def test_partial_failure_returns_nonzero(self, cmd, profile, stack_outputs, tmp_path, monkeypatch):
        pkg = self._package(tmp_path)
        fake_s3 = FakeS3(fail_key_tokens=("windows",))
        monkeypatch.setattr(DistributeCommand, "_s3_client", staticmethod(lambda region: fake_s3))
        console = Console(file=io.StringIO(), force_terminal=False, width=200)

        result = cmd._distribute_per_os(pkg, profile, ["windows", "linux-x64"], 24, console)

        assert result != 0, "a failed per-OS upload must not exit 0 even when other platforms succeeded"
        assert any("linux-x64" in key for key in fake_s3.uploads), "the healthy platform still uploads"

    def test_all_success_returns_zero(self, cmd, profile, stack_outputs, tmp_path, monkeypatch):
        pkg = self._package(tmp_path)
        fake_s3 = FakeS3()
        monkeypatch.setattr(DistributeCommand, "_s3_client", staticmethod(lambda region: fake_s3))
        console = Console(file=io.StringIO(), force_terminal=False, width=200)

        result = cmd._distribute_per_os(pkg, profile, ["windows", "linux-x64"], 24, console)

        assert result == 0
