# ABOUTME: Regression tests - distribute must accept IDC zero-binary (config/installer-only) builds
# ABOUTME: Both the presigned and landing-page paths previously exited 1 telling the admin to rebuild

"""Regression tests for IDC zero-binary distribution (finding x1).

package.py supports a zero-binary mode (IDC auth without quota enforcement):
the build intentionally contains NO executables — users authenticate via
``aws sso login`` and the package ships only config.json, install scripts,
docs, and (in sidecar mode) collector-config.yaml. ``_scan_distributions``
accepts such builds, but both distribution paths then rejected them:

- ``_upload_landing_page_packages`` detected platforms only via executables and
  exited 1 with "No platform packages found! Run: poetry run ccwb package first"
- ``_create_distribution`` exited 1 with "No platform executables found!"

...remediation that would just rebuild another zero-binary package. These
tests assert both paths now detect the zero-binary case from the profile
(effective_auth_type == "idc", no quota endpoint) and distribute the
config-only package, while non-IDC binary-less builds still fail loudly.
"""

import io
import zipfile

import boto3
import pytest
from rich.console import Console

from claude_code_with_bedrock.cli.commands.distribute import DistributeCommand
from claude_code_with_bedrock.config import Profile


class FakeS3:
    """Records uploads and captures zip member names at upload time."""

    def __init__(self):
        self.uploads = []
        self.zip_contents = {}

    def upload_file(self, Filename=None, Bucket=None, Key=None, ExtraArgs=None, Config=None, Callback=None):
        if Filename and str(Filename).endswith(".zip"):
            with zipfile.ZipFile(Filename) as zf:
                self.zip_contents[Key] = zf.namelist()
        self.uploads.append(Key)

    def delete_object(self, Bucket=None, Key=None):
        pass

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):
        return f"https://example.com/{Params['Key']}"


@pytest.fixture
def cmd():
    command = DistributeCommand.__new__(DistributeCommand)
    command.option = {
        "expires-hours": "24",
        "per-os": False,
        "allowed-ips": None,
        "show-qr": False,
    }.get
    return command


@pytest.fixture
def idc_zero_binary_profile():
    """IDC auth, no quota endpoint → package.py builds no binaries at all."""
    return Profile(
        name="idc-test",
        provider_domain="",
        client_id="",
        credential_storage="keyring",
        aws_region="us-east-1",
        identity_pool_name="idc-pool",
        enable_distribution=False,
        auth_type="idc",
    )


@pytest.fixture
def zero_binary_package(tmp_path):
    """What package.py emits in zero-binary mode: config + installer + docs, no binaries."""
    pkg = tmp_path / "dist" / "idc-test" / "2026-06-01-120000"
    pkg.mkdir(parents=True)
    (pkg / "config.json").write_text('{"profile": "idc-test"}')
    (pkg / "install.sh").write_text("#!/bin/bash\necho idc install")
    (pkg / "README.md").write_text("# README")
    (pkg / "collector-config.yaml").write_text("receivers: {}")
    settings = pkg / "claude-settings"
    settings.mkdir()
    (settings / "settings.json").write_text("{}")
    return pkg


@pytest.fixture
def no_aws_clients(monkeypatch):
    """No real AWS client may be constructed in these tests."""
    monkeypatch.setattr(
        boto3, "client", lambda service, *a, **k: (_ for _ in ()).throw(RuntimeError(f"unexpected client: {service}"))
    )


class TestZeroBinaryDetection:
    def test_idc_without_quota_is_zero_binary(self, idc_zero_binary_profile):
        assert DistributeCommand._is_idc_zero_binary(idc_zero_binary_profile) is True

    def test_idc_with_quota_is_not_zero_binary(self, idc_zero_binary_profile):
        idc_zero_binary_profile.quota_api_endpoint = "https://quota.example.com"
        assert DistributeCommand._is_idc_zero_binary(idc_zero_binary_profile) is False

    def test_oidc_is_not_zero_binary(self):
        profile = Profile(
            name="oidc",
            provider_domain="example.okta.com",
            client_id="c",
            credential_storage="keyring",
            aws_region="us-east-1",
            identity_pool_name="pool",
        )
        assert DistributeCommand._is_idc_zero_binary(profile) is False


class TestPresignedPathZeroBinary:
    """_create_distribution must distribute the config-only build, not exit 1."""

    def test_zero_binary_build_distributes_locally(
        self, cmd, idc_zero_binary_profile, zero_binary_package, no_aws_clients, tmp_path, monkeypatch
    ):
        """Pre-fix this returned 1 ('No platform executables found!')."""
        monkeypatch.chdir(tmp_path)  # disabled distribution saves to ./dist
        console = Console(file=io.StringIO(), force_terminal=False, width=200)

        result = cmd._create_distribution(idc_zero_binary_profile, console, zero_binary_package)

        assert result == 0, "an IDC zero-binary build is a valid distributable package"

    def test_zero_binary_archive_contains_config_and_installer(
        self, cmd, idc_zero_binary_profile, zero_binary_package, no_aws_clients, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        console = Console(file=io.StringIO(), force_terminal=False, width=200)

        result = cmd._create_distribution(idc_zero_binary_profile, console, zero_binary_package)

        assert result == 0
        zips = sorted((tmp_path / "dist").glob("claude-code-package-*.zip"))
        assert zips, "zero-binary path must still produce the archive"
        with zipfile.ZipFile(zips[0]) as zf:
            names = {name.split("/")[-1] for name in zf.namelist()}
        assert "config.json" in names
        assert "install.sh" in names
        assert "collector-config.yaml" in names

    def test_zero_binary_does_not_prompt_about_windows(
        self, cmd, idc_zero_binary_profile, zero_binary_package, no_aws_clients, tmp_path, monkeypatch
    ):
        """No binaries means the 'Continue without Windows support?' prompt is meaningless."""
        monkeypatch.chdir(tmp_path)
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, width=200)

        result = cmd._create_distribution(idc_zero_binary_profile, console, zero_binary_package)

        assert result == 0
        assert "Continue without Windows support" not in buffer.getvalue()
        assert "Windows support not included" not in buffer.getvalue()

    def test_non_idc_binary_less_build_still_fails(self, cmd, zero_binary_package, no_aws_clients, monkeypatch):
        """Guard preserved: an OIDC build with no executables is a broken build."""
        oidc_profile = Profile(
            name="oidc",
            provider_domain="example.okta.com",
            client_id="c",
            credential_storage="keyring",
            aws_region="us-east-1",
            identity_pool_name="pool",
            enable_distribution=False,
        )
        console = Console(file=io.StringIO(), force_terminal=False, width=200)

        result = cmd._create_distribution(oidc_profile, console, zero_binary_package)

        assert result == 1


class TestLandingPagePathZeroBinary:
    """_upload_landing_page_packages must upload config-only zips for zero-binary builds."""

    @pytest.fixture
    def landing_profile(self, idc_zero_binary_profile):
        idc_zero_binary_profile.enable_distribution = True
        idc_zero_binary_profile.distribution_type = "landing-page"
        return idc_zero_binary_profile

    @pytest.fixture
    def stack_outputs(self, monkeypatch):
        monkeypatch.setattr(
            "claude_code_with_bedrock.cli.commands.distribute.get_stack_outputs",
            lambda *args, **kwargs: {
                "DistributionBucket": "dist-bucket",
                "DistributionURL": "https://landing.example.com",
            },
        )

    def test_zero_binary_uploads_config_only_packages(
        self, cmd, landing_profile, zero_binary_package, stack_outputs, no_aws_clients, monkeypatch
    ):
        """Pre-fix this returned 1 ('No platform packages found!')."""
        fake_s3 = FakeS3()
        monkeypatch.setattr(DistributeCommand, "_s3_client", staticmethod(lambda region: fake_s3))
        console = Console(file=io.StringIO(), force_terminal=False, width=200)

        result = cmd._upload_landing_page_packages(landing_profile, console, zero_binary_package)

        assert result == 0, "zero-binary landing-page distribution must succeed"
        # install.sh present → linux + mac families, plus the all-platforms bundle
        assert "packages/linux/latest.zip" in fake_s3.uploads
        assert "packages/mac/latest.zip" in fake_s3.uploads
        assert "packages/all-platforms/latest.zip" in fake_s3.uploads
        # No install.bat in a zero-binary build → no windows package
        assert "packages/windows/latest.zip" not in fake_s3.uploads

    def test_zero_binary_zip_contains_config_and_installer(
        self, cmd, landing_profile, zero_binary_package, stack_outputs, no_aws_clients, monkeypatch
    ):
        fake_s3 = FakeS3()
        monkeypatch.setattr(DistributeCommand, "_s3_client", staticmethod(lambda region: fake_s3))
        console = Console(file=io.StringIO(), force_terminal=False, width=200)

        result = cmd._upload_landing_page_packages(landing_profile, console, zero_binary_package)

        assert result == 0
        names = {name.split("/")[-1] for name in fake_s3.zip_contents["packages/all-platforms/latest.zip"]}
        assert "config.json" in names
        assert "install.sh" in names
        assert "collector-config.yaml" in names
        assert "settings.json" in names

    def test_non_idc_binary_less_build_still_fails(
        self, cmd, zero_binary_package, stack_outputs, no_aws_clients, monkeypatch
    ):
        """Guard preserved: OIDC landing-page distribution with no binaries fails."""
        oidc_profile = Profile(
            name="oidc",
            provider_domain="example.okta.com",
            client_id="c",
            credential_storage="keyring",
            aws_region="us-east-1",
            identity_pool_name="pool",
            enable_distribution=True,
            distribution_type="landing-page",
        )
        fake_s3 = FakeS3()
        monkeypatch.setattr(DistributeCommand, "_s3_client", staticmethod(lambda region: fake_s3))
        console = Console(file=io.StringIO(), force_terminal=False, width=200)

        result = cmd._upload_landing_page_packages(oidc_profile, console, zero_binary_package)

        assert result == 1
        assert fake_s3.uploads == []
