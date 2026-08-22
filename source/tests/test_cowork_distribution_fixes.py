# ABOUTME: Regression tests for Claude Desktop distribution and model-ID resolution
# ABOUTME: Covers per-OS Cowork manifest parity, regional presigned URLs, and CRIS-pinned model IDs

"""Regression tests for three defects that broke Claude Desktop deployment outside
macOS + us-east-1.

1. Per-OS distribution archives omitted every Cowork artifact, so ``install.bat``
   silently skipped its ``if exist`` copy of ``cowork-credential-helper.cmd`` and
   there was no ``cowork-3p.reg`` to import. Claude Desktop could not be configured
   from a distributed package at all. See
   ``.claude/rules/distribution-manifest-parity.md``, which this violated.

2. Presigned URLs were signed against the global ``s3.amazonaws.com`` endpoint. For
   a bucket outside ``us-east-1`` that 307-redirects to the regional endpoint, and
   because ``host`` is a signed header the redirect invalidates the signature —
   every URL returned 403 Forbidden.

3. ``inferenceModels`` was emitted as bare tier aliases, leaving Claude Desktop to
   derive a CRIS prefix from ``inferenceBedrockRegion``. In ``ap-south-1`` that
   yields ``apac.anthropic.claude-opus-5``, which does not exist, and Bedrock
   rejects it with ``400 The provided model identifier is invalid``.
"""

import pytest

from claude_code_with_bedrock.cli.commands.distribute import DistributeCommand
from claude_code_with_bedrock.cli.utils.cowork_3p import (
    COWORK_DEFAULT_ALIASES,
    build_inference_models,
    build_inference_models_explicit,
    build_mdm_config,
)


@pytest.fixture
def cmd():
    """DistributeCommand instance without invoking CLI machinery."""
    return DistributeCommand.__new__(DistributeCommand)


class TestPerOsCoworkManifest:
    """Defect 1 — Cowork artifacts must reach the per-OS archives."""

    def test_windows_ships_helper_and_registry(self):
        files = DistributeCommand.COWORK_FILES["windows"]
        assert "cowork-credential-helper.cmd" in files, "install.bat needs the helper it points at"
        assert "cowork-3p.reg" in files, "no .reg means the MDM policy cannot be applied"

    def test_macos_ships_helper_and_mobileconfig(self):
        for platform in ("macos-arm64", "macos-intel"):
            files = DistributeCommand.COWORK_FILES[platform]
            assert "cowork-credential-helper.sh" in files
            assert "cowork-3p.mobileconfig" in files

    def test_every_platform_covered(self):
        """A platform missing from COWORK_FILES silently ships no Desktop config."""
        assert set(DistributeCommand.COWORK_FILES) == set(DistributeCommand.PLATFORM_FILES)

    def test_windows_does_not_ship_posix_helper(self):
        assert "cowork-credential-helper.sh" not in DistributeCommand.COWORK_FILES["windows"]

    def test_posix_does_not_ship_windows_artifacts(self):
        for platform in ("macos-arm64", "linux-x64"):
            files = DistributeCommand.COWORK_FILES[platform]
            assert "cowork-credential-helper.cmd" not in files
            assert "cowork-3p.reg" not in files

    def test_archive_includes_cowork_files(self, cmd, tmp_path):
        """End-to-end: a package with Cowork artifacts must yield them in the zip."""
        import zipfile

        for name in (
            "credential-process-windows.exe",
            "otel-helper-windows.exe",
            "install.bat",
            "config.json",
            "cowork-credential-helper.cmd",
            "cowork-3p.reg",
            "cowork-3p-config.json",
        ):
            (tmp_path / name).write_text("x")

        archives = cmd._create_per_os_archives(tmp_path)
        windows = [a for a in archives if a[0] == "windows"]
        assert windows, "windows archive was not produced"

        with zipfile.ZipFile(windows[0][-1]) as zf:
            names = {n.split("/")[-1] for n in zf.namelist()}
        assert "cowork-credential-helper.cmd" in names
        assert "cowork-3p.reg" in names


class TestRegionalPresignEndpoint:
    """Defect 2 — presigned URLs must carry the regional host."""

    @pytest.mark.parametrize("region", ["ap-south-1", "eu-west-1", "us-west-2", "us-east-1"])
    def test_endpoint_is_regional(self, region):
        client = DistributeCommand._s3_client(region)
        assert client.meta.endpoint_url == f"https://s3.{region}.amazonaws.com"

    def test_signature_is_sigv4(self):
        client = DistributeCommand._s3_client("ap-south-1")
        assert client.meta.config.signature_version == "s3v4"

    def test_china_partition_suffix(self):
        client = DistributeCommand._s3_client("cn-north-1")
        assert client.meta.endpoint_url == "https://s3.cn-north-1.amazonaws.com.cn"

    def test_presigned_url_host_is_regional(self):
        """The signed URL itself must not use the global endpoint."""
        client = DistributeCommand._s3_client("ap-south-1")
        url = client.generate_presigned_url(
            "get_object", Params={"Bucket": "example-bucket", "Key": "pkg.zip"}, ExpiresIn=60
        )
        assert "s3.ap-south-1.amazonaws.com" in url
        assert "example-bucket.s3.amazonaws.com" not in url


class TestExplicitModelIds:
    """Defect 3 — tier aliases must resolve to real CRIS model IDs."""

    def test_global_prefix_yields_global_ids(self):
        models = build_inference_models_explicit(COWORK_DEFAULT_ALIASES, "global")
        assert models, "no models resolved for the global CRIS profile"
        for entry in models:
            assert isinstance(entry, dict), "tier aliases must become object entries"
            assert entry["name"].startswith("global."), entry["name"]
            assert entry["anthropicFamilyTier"] in ("opus", "sonnet", "haiku")

    def test_no_bare_aliases_survive(self):
        """A bare alias would let Desktop derive a prefix — the original bug."""
        models = build_inference_models_explicit(COWORK_DEFAULT_ALIASES, "global")
        assert not any(isinstance(m, str) and m in COWORK_DEFAULT_ALIASES for m in models)

    def test_each_tier_gets_exactly_one_default(self):
        models = build_inference_models_explicit(COWORK_DEFAULT_ALIASES, "global")
        defaults = [m for m in models if isinstance(m, dict) and m.get("isFamilyDefault")]
        tiers = [m["anthropicFamilyTier"] for m in defaults]
        assert len(tiers) == len(set(tiers)), "a tier was marked default more than once"

    def test_explicit_model_ids_pass_through(self):
        models = build_inference_models_explicit(["global.anthropic.claude-sonnet-5"], "global")
        assert models == ["global.anthropic.claude-sonnet-5"]

    def test_mdm_config_pins_ids_when_prefix_given(self):
        config = build_mdm_config(
            bedrock_region="ap-south-1", model_aliases=COWORK_DEFAULT_ALIASES, cris_prefix="global"
        )
        for entry in config["inferenceModels"]:
            assert isinstance(entry, dict)
            assert entry["name"].startswith("global.")

    def test_mdm_config_without_prefix_keeps_legacy_behaviour(self):
        """Callers with no profile context must not change shape."""
        config = build_mdm_config(bedrock_region="ap-south-1", model_aliases=COWORK_DEFAULT_ALIASES)
        assert config["inferenceModels"] == build_inference_models(COWORK_DEFAULT_ALIASES)

    def test_apac_never_emits_a_nonexistent_opus_5(self):
        """apac has no Opus 5 profile; resolution must not invent one."""
        models = build_inference_models_explicit(["opus"], "apac")
        for entry in models:
            name = entry["name"] if isinstance(entry, dict) else entry
            assert name != "apac.anthropic.claude-opus-5"
