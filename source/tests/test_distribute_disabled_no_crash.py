# ABOUTME: Regression test - ccwb distribute with enable_distribution=False must not crash
# ABOUTME: Guards the if/else structure around the S3 presign + Parameter Store block

"""Regression test for the distribution-disabled path of ``ccwb distribute``.

Defect: in ``_create_distribution`` the presigned-URL / Parameter Store block sat
OUTSIDE the ``if profile.enable_distribution:`` body, and the intended
"Distribution not enabled - save locally" branch was misattached as the ``else``
of the ``ssm.put_parameter`` try/except. With ``enable_distribution=False`` the
command referenced ``s3`` / ``bucket_name`` / ``package_key`` (defined only in
the enabled branch) and died with ``NameError`` instead of saving the package
to ``dist/`` and exiting 0.

These tests drive ``_create_distribution`` with a disabled profile and assert
the graceful local-save branch runs: no AWS S3/SSM calls, package copied to
``dist/``, guidance printed, exit code 0.
"""

import io
import zipfile

import boto3
import pytest
from rich.console import Console

from claude_code_with_bedrock.cli.commands.distribute import DistributeCommand
from claude_code_with_bedrock.config import Profile


@pytest.fixture
def cmd():
    """DistributeCommand instance without invoking CLI machinery."""
    command = DistributeCommand.__new__(DistributeCommand)
    options = {
        "expires-hours": "24",
        "per-os": False,
        "allowed-ips": None,
        "show-qr": False,
    }
    command.option = options.get  # stub cleo option lookup
    return command


@pytest.fixture
def disabled_profile():
    """Profile with distribution features disabled (the crashing configuration)."""
    return Profile(
        name="test",
        provider_domain="example.okta.com",
        client_id="client-123",
        credential_storage="keyring",
        aws_region="us-east-1",
        identity_pool_name="test-pool",
        enable_distribution=False,
    )


@pytest.fixture
def package_dir(tmp_path):
    """A minimal packaged build covering all platforms (skips interactive prompts)."""
    pkg = tmp_path / "package"
    pkg.mkdir()
    for name in (
        "credential-process-macos-arm64",
        "credential-process-windows.exe",
        "credential-process-linux-x64",
        "install.sh",
        "install.bat",
        "config.json",
    ):
        (pkg / name).write_text("stub")
    return pkg


@pytest.fixture
def aws_calls(monkeypatch):
    """Record boto3 client requests and refuse to create real clients.

    The disabled path must never build an S3 or SSM client. The CodeBuild
    freshness probe is best-effort (wrapped in ``except Exception: pass``),
    so raising here keeps the test hermetic either way.
    """
    calls = []

    def _no_client(service, *args, **kwargs):
        calls.append(service)
        raise RuntimeError(f"unexpected AWS client in disabled path: {service}")

    monkeypatch.setattr(boto3, "client", _no_client)
    return calls


class TestDistributionDisabledPath:
    def test_no_crash_and_exit_zero(self, cmd, disabled_profile, package_dir, aws_calls, tmp_path, monkeypatch):
        """enable_distribution=False must take the local-save branch, not NameError.

        Pre-fix this raised ``NameError: name 's3' is not defined`` from the
        unconditional presigned-URL block.
        """
        monkeypatch.chdir(tmp_path)  # local save writes to ./dist
        console = Console(file=io.StringIO(), force_terminal=False)

        result = cmd._create_distribution(disabled_profile, console, package_dir)

        assert result == 0, "graceful disabled-path no-op must honour the exit-code contract (0 = success)"

    def test_package_saved_locally(self, cmd, disabled_profile, package_dir, aws_calls, tmp_path, monkeypatch):
        """The archive must land in dist/ and contain the packaged files."""
        monkeypatch.chdir(tmp_path)
        console = Console(file=io.StringIO(), force_terminal=False)

        result = cmd._create_distribution(disabled_profile, console, package_dir)

        assert result == 0
        zips = sorted((tmp_path / "dist").glob("claude-code-package-*.zip"))
        assert zips, "disabled path must save the package under dist/"
        with zipfile.ZipFile(zips[0]) as zf:
            names = {n.split("/")[-1] for n in zf.namelist()}
        assert "install.sh" in names
        assert "config.json" in names

    def test_prints_local_details_and_enable_guidance(
        self, cmd, disabled_profile, package_dir, aws_calls, tmp_path, monkeypatch
    ):
        """The graceful branch must tell the admin where the package is and how to enable distribution."""
        monkeypatch.chdir(tmp_path)
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, width=200)

        result = cmd._create_distribution(disabled_profile, console, package_dir)

        assert result == 0
        output = buffer.getvalue()
        assert "Package saved locally" in output
        assert "ccwb init" in output, "must explain how to enable distribution"
        assert "deploy distribution" in output

    def test_no_s3_or_ssm_clients_created(self, cmd, disabled_profile, package_dir, aws_calls, tmp_path, monkeypatch):
        """Disabled path must not touch S3 or Parameter Store at all."""
        monkeypatch.chdir(tmp_path)
        console = Console(file=io.StringIO(), force_terminal=False)

        cmd._create_distribution(disabled_profile, console, package_dir)

        assert "s3" not in aws_calls
        assert "ssm" not in aws_calls
