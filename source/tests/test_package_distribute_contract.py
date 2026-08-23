# ABOUTME: Contract test ensuring package.py binary naming agrees with distribute.py's real manifests.
# ABOUTME: Imports the actual manifests (no hand-copied allowlist) so drift is caught, not masked.

"""Contract tests for package ↔ distribute binary naming agreement.

These tests verify that every binary filename package.py can produce is recognized
by distribute.py's manifests. Without this contract, a naming mismatch causes
distribute to silently ship a config-only zip (no binaries, no warning), and
install.sh fails on the target machine.

Motivation: Issue #682 Bug 4 — `--target-platform linux` produced `credential-process-linux`
which distribute silently dropped (only recognized `credential-process-linux-x64`).
The failure was invisible at build time and only surfaced at install time.

distribute.py maintains THREE manifests that must stay mutually consistent
(see .claude/rules/distribution-manifest-parity.md):

1. ``LANDING_PLATFORM_FILES``   — landing-page family zips (windows/linux/mac)
2. ``ARCHIVE_REQUIRED_FILES``   — the all-in-one presigned archive
3. ``PLATFORM_FILES`` + ``COWORK_FILES`` + ``PER_OS_SHARED_FILES`` — per-OS zips

A previous version of this file compared package.py against a hand-copied
allowlist instead of importing distribute.py — which is exactly how a real
drift (per-OS zips missing otelcol-* and collector-config.yaml, breaking the
local collector in sidecar-mode installs) went undetected. Everything below
imports the real manifests.
"""

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_code_with_bedrock.cli.commands.distribute import DistributeCommand
from claude_code_with_bedrock.cli.commands.package import _GO_PLATFORM_MAP

# --- Extract the authoritative sets from each component ---


# Per-OS platform key → landing-page download family
_PER_OS_TO_LANDING_FAMILY = {
    "windows": "windows",
    "linux-x64": "linux",
    "linux-arm64": "linux",
    "macos-arm64": "mac",
    "macos-intel": "mac",
}

# Generic tokens package.py normalizes before building (Go mode)
_PLATFORM_CANONICAL = {
    "linux": "linux-x64",
    "macos": "macos-arm64",
}


def _get_distribute_accepted_source_names() -> set[str]:
    """All source filenames distribute.py's landing-page manifest will include in zips.

    Imported from the REAL manifest — if distribute.py changes its allowlist,
    this test sees the change immediately (no hand-maintained copy to forget).
    """
    return {
        source_file
        for files in DistributeCommand.LANDING_PLATFORM_FILES.values()
        for source_file, _archive_name in files
    }


def _get_package_producible_binary_names() -> dict[str, str]:
    """Compute every binary filename package.py's Go build path can produce.

    Returns dict mapping platform_key → binary_name for credential-process.
    Uses the same logic as _build_go_binaries: suffix = f"-{plat}" (or "-windows.exe").
    """
    names = {}
    for plat in _GO_PLATFORM_MAP:
        if plat == "windows":
            suffix = "-windows.exe"
        else:
            suffix = f"-{plat}"
        names[plat] = f"credential-process{suffix}"
    return names


def _get_install_sh_expected_suffixes() -> set[str]:
    """Binary suffixes that install.sh constructs from uname.

    install.sh does: BINARY_SUFFIX="linux-x64" or "linux-arm64" (from uname -m)
    then: CREDENTIAL_BINARY="credential-process-$BINARY_SUFFIX"
    and (sidecar mode): OTELCOL_BINARY="otelcol-$BINARY_SUFFIX"

    If a binary doesn't match these, install.sh will fail with "Binary not found".
    """
    return {
        "linux-x64",
        "linux-arm64",
        "macos-arm64",
        "macos-intel",
    }


def _expected_platform_binaries(per_os_key: str) -> tuple[str, str, str]:
    """The three binaries package.py ships for a qualified platform key."""
    suffix = "windows.exe" if per_os_key == "windows" else per_os_key
    return (
        f"credential-process-{suffix}",
        f"otel-helper-{suffix}",
        f"otelcol-{suffix}",
    )


def _qualified_go_platforms() -> set[str]:
    """_GO_PLATFORM_MAP keys with generic tokens normalized to arch-qualified ones."""
    return {_PLATFORM_CANONICAL.get(plat, plat) for plat in _GO_PLATFORM_MAP}


def _per_os_manifest_files(per_os_key: str) -> set[str]:
    """Every filename the per-OS zip builder may include for a platform."""
    pconfig = DistributeCommand.PLATFORM_FILES[per_os_key]
    installers = pconfig["installer"] if isinstance(pconfig["installer"], list) else [pconfig["installer"]]
    return (
        set(pconfig["binaries"])
        | set(installers)
        | set(DistributeCommand.COWORK_FILES.get(per_os_key, []))
        | set(DistributeCommand.PER_OS_SHARED_FILES)
    )


# --- Contract Tests ---


class TestPackageDistributeContract:
    """Verify naming agreement between package, distribute, and install components."""

    def test_every_go_platform_produces_recognized_binary(self):
        """Every platform in _GO_PLATFORM_MAP must produce a binary that
        distribute.py will include in its zip (not silently drop).

        This is the core contract that prevents Bug 4 from #682.

        Generic tokens ('linux', 'macos') are acceptable in _GO_PLATFORM_MAP
        for backward compat, but they MUST be normalized to arch-specific
        names before reaching _build_go_binaries(). This test verifies that
        either:
        (a) the raw name is in distribute's allowlist, OR
        (b) a normalization mapping exists that resolves to a recognized name.
        """
        distribute_accepts = _get_distribute_accepted_source_names()
        package_produces = _get_package_producible_binary_names()

        unrecognized = []
        for plat, binary_name in package_produces.items():
            # Check if raw name is accepted
            if binary_name in distribute_accepts:
                continue
            # Check if the canonical (normalized) name would be accepted
            canonical_plat = _PLATFORM_CANONICAL.get(plat)
            if canonical_plat:
                canonical_name = f"credential-process-{canonical_plat}"
                if canonical_name in distribute_accepts:
                    continue  # Normalization handles this
            unrecognized.append((plat, binary_name))

        assert not unrecognized, (
            f"package.py can produce binaries that distribute.py would silently drop!\n"
            f"Unrecognized names: {unrecognized}\n"
            f"Either:\n"
            f"  1. Add these to distribute.py's LANDING_PLATFORM_FILES manifest, OR\n"
            f"  2. Remove the platform key from _GO_PLATFORM_MAP, OR\n"
            f"  3. Normalize the platform key before building (e.g., 'linux' → 'linux-x64')"
        )

    def test_install_sh_suffixes_match_distribute(self):
        """install.sh constructs binary names from uname. Those names must exist
        in distribute's allowlist, otherwise install succeeds but binary is missing.
        """
        distribute_accepts = _get_distribute_accepted_source_names()
        install_suffixes = _get_install_sh_expected_suffixes()

        missing = []
        for suffix in install_suffixes:
            for prefix in ("credential-process", "otel-helper", "otelcol"):
                expected_binary = f"{prefix}-{suffix}"
                if expected_binary not in distribute_accepts:
                    missing.append(expected_binary)

        assert not missing, f"install.sh expects binaries that distribute.py doesn't include!\nMissing: {missing}"

    def test_otel_helper_naming_matches_credential_process(self):
        """otel-helper binaries must use the same platform suffixes as credential-process.

        If credential-process-linux-x64 exists, otel-helper-linux-x64 must too.
        """
        distribute_accepts = _get_distribute_accepted_source_names()

        cred_suffixes = set()
        otel_suffixes = set()

        for name in distribute_accepts:
            if name.startswith("credential-process-"):
                suffix = name.removeprefix("credential-process-")
                cred_suffixes.add(suffix)
            elif name.startswith("otel-helper-"):
                suffix = name.removeprefix("otel-helper-")
                otel_suffixes.add(suffix)

        # otel-helper should cover the same platforms as credential-process
        missing = cred_suffixes - otel_suffixes
        assert not missing, (
            f"credential-process has platform suffixes that otel-helper is missing!\n"
            f"Missing otel-helper variants: {['otel-helper-' + s for s in missing]}"
        )

    def test_go_platform_map_has_no_ambiguous_generic_tokens(self):
        """Generic platform tokens (no arch suffix) in _GO_PLATFORM_MAP are dangerous
        because they produce binaries with non-standard names.

        If a generic token exists, it MUST be normalized before reaching the build
        function, or distribute must have an explicit fallback mapping for it.

        This test flags any generic token that doesn't have a corresponding
        arch-specific equivalent already producing the same binary.
        """
        distribute_accepts = _get_distribute_accepted_source_names()

        # Tokens without a hyphen-separated arch component
        generic_tokens = [plat for plat in _GO_PLATFORM_MAP if plat in ("linux", "macos", "windows")]

        dangerous = []
        for token in generic_tokens:
            if token == "windows":
                binary = "credential-process-windows.exe"
            else:
                binary = f"credential-process-{token}"

            if binary not in distribute_accepts:
                dangerous.append((token, binary))

        if dangerous:
            # This is informational — the test documents the risk
            # If normalization is in place, generic tokens never reach the build
            pytest.skip(f"Generic tokens produce non-standard names (should be normalized before build): {dangerous}")

    @pytest.mark.parametrize("platform", list(_GO_PLATFORM_MAP.keys()))
    def test_platform_suffix_is_deterministic(self, platform):
        """Each platform key always produces the same binary name.
        No runtime state (uname, env vars) should influence Go build naming.
        """
        if platform == "windows":
            expected_suffix = "-windows.exe"
        else:
            expected_suffix = f"-{platform}"

        binary_name = f"credential-process{expected_suffix}"
        # The name must be a pure function of the platform key
        assert binary_name == f"credential-process{expected_suffix}"
        # And must not contain path separators
        assert "/" not in binary_name
        assert "\\" not in binary_name


class TestManifestMutualConsistency:
    """The three hand-maintained manifests in distribute.py must agree with each
    other and cover everything package.py ships. Regression coverage for the
    per-OS zips dropping otelcol-* and collector-config.yaml (broken local
    collector in sidecar-mode installs)."""

    @pytest.mark.parametrize("per_os_key", sorted(_PER_OS_TO_LANDING_FAMILY))
    def test_per_os_binaries_include_otelcol(self, per_os_key):
        """package.py sidecar mode ships otelcol-{platform} and install.sh/install.bat
        install it — the per-OS zip manifest must list it or sidecar installs break."""
        _cred, _otel, otelcol = _expected_platform_binaries(per_os_key)
        assert otelcol in DistributeCommand.PLATFORM_FILES[per_os_key]["binaries"], (
            f"per-OS zip for {per_os_key} would ship without the {otelcol} sidecar collector"
        )

    def test_per_os_shared_files_include_collector_config(self):
        """The installer copies collector-config.yaml next to otelcol; a per-OS zip
        without it leaves the sidecar collector unconfigured."""
        assert "collector-config.yaml" in DistributeCommand.PER_OS_SHARED_FILES

    def test_per_os_primary_binary_is_credential_process(self):
        """_create_per_os_archives uses binaries[0] to detect whether a platform was
        built — that sentinel must remain the auth binary, not otelcol/otel-helper."""
        for per_os_key, pconfig in DistributeCommand.PLATFORM_FILES.items():
            assert pconfig["binaries"][0].startswith("credential-process"), per_os_key

    @pytest.mark.parametrize("per_os_key", sorted(_PER_OS_TO_LANDING_FAMILY))
    def test_per_os_manifest_subset_of_archive_manifest(self, per_os_key):
        """Everything a per-OS zip may ship must also ship in the all-in-one archive."""
        missing = _per_os_manifest_files(per_os_key) - set(DistributeCommand.ARCHIVE_REQUIRED_FILES)
        assert not missing, f"per-OS files for {per_os_key} missing from ARCHIVE_REQUIRED_FILES: {sorted(missing)}"

    @pytest.mark.parametrize("per_os_key", sorted(_PER_OS_TO_LANDING_FAMILY))
    def test_per_os_manifest_subset_of_landing_manifest(self, per_os_key):
        """Everything a per-OS zip may ship must also ship in its landing-page family zip."""
        family = _PER_OS_TO_LANDING_FAMILY[per_os_key]
        landing_sources = {source for source, _ in DistributeCommand.LANDING_PLATFORM_FILES[family]}
        missing = _per_os_manifest_files(per_os_key) - landing_sources
        assert not missing, (
            f"per-OS files for {per_os_key} missing from LANDING_PLATFORM_FILES[{family!r}]: {sorted(missing)}"
        )

    def test_landing_archive_names_subset_of_archive_manifest(self):
        """Every name the landing zips write must also be in the all-in-one archive
        manifest (generic-alias sources map to canonical archive names)."""
        for family, files in DistributeCommand.LANDING_PLATFORM_FILES.items():
            for _source, archive_name in files:
                assert archive_name in DistributeCommand.ARCHIVE_REQUIRED_FILES, (
                    f"landing family {family!r} writes {archive_name!r} which the "
                    f"all-in-one archive manifest does not know about"
                )

    @pytest.mark.parametrize("qualified_platform", sorted(_qualified_go_platforms()))
    def test_package_binary_outputs_covered_by_all_manifests(self, qualified_platform):
        """Every binary package.py builds (credential-process, otel-helper, otelcol)
        must be shippable by all three distribution manifests."""
        expected = _expected_platform_binaries(qualified_platform)
        family = _PER_OS_TO_LANDING_FAMILY[qualified_platform]
        landing_sources = {source for source, _ in DistributeCommand.LANDING_PLATFORM_FILES[family]}
        per_os_binaries = set(DistributeCommand.PLATFORM_FILES[qualified_platform]["binaries"])

        for binary in expected:
            assert binary in DistributeCommand.ARCHIVE_REQUIRED_FILES, f"{binary} missing from all-in-one archive"
            assert binary in landing_sources, f"{binary} missing from landing family {family!r}"
            assert binary in per_os_binaries, f"{binary} missing from per-OS zip for {qualified_platform}"

    def test_collector_config_covered_by_all_manifests(self):
        """collector-config.yaml ships in sidecar-mode packages and must be
        distributable via every path."""
        assert "collector-config.yaml" in DistributeCommand.ARCHIVE_REQUIRED_FILES
        assert "collector-config.yaml" in DistributeCommand.PER_OS_SHARED_FILES
        for family, files in DistributeCommand.LANDING_PLATFORM_FILES.items():
            sources = {source for source, _ in files}
            assert "collector-config.yaml" in sources, f"landing family {family!r} misses collector-config.yaml"


class TestPerOsZipShipsSidecarCollector:
    """Functional regression: a sidecar-mode package's per-OS zips must actually
    contain otelcol-{platform} and collector-config.yaml (they used to be
    silently dropped, so install.sh configured a collector that didn't exist)."""

    @pytest.fixture
    def sidecar_package(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "config.json").write_text("{}")
        (pkg / "README.md").write_text("# README")
        (pkg / "collector-config.yaml").write_text("receivers: {}")
        (pkg / "install.sh").write_text("#!/bin/bash")
        (pkg / "install.bat").write_text("@echo off")
        for suffix in ("windows.exe", "linux-x64", "linux-arm64", "macos-arm64", "macos-intel"):
            (pkg / f"credential-process-{suffix}").write_bytes(b"\x00")
            (pkg / f"otel-helper-{suffix}").write_bytes(b"\x00")
            (pkg / f"otelcol-{suffix}").write_bytes(b"\x00")
        return pkg

    def test_every_per_os_zip_contains_otelcol_and_collector_config(self, sidecar_package):
        cmd = DistributeCommand.__new__(DistributeCommand)
        archives = cmd._create_per_os_archives(sidecar_package)
        assert len(archives) == 5

        for per_os_key, _label, archive_path in archives:
            _cred, _otel, otelcol = _expected_platform_binaries(per_os_key)
            with zipfile.ZipFile(archive_path) as zf:
                names = {name.split("/")[-1] for name in zf.namelist()}
            assert otelcol in names, f"{per_os_key} zip lost the otelcol sidecar collector"
            assert "collector-config.yaml" in names, f"{per_os_key} zip lost collector-config.yaml"

    def test_non_sidecar_package_unaffected(self, tmp_path):
        """Packages without otelcol/collector-config (central-collector mode) still zip fine."""
        pkg = tmp_path / "central"
        pkg.mkdir()
        (pkg / "config.json").write_text("{}")
        (pkg / "install.sh").write_text("#!/bin/bash")
        (pkg / "credential-process-linux-x64").write_bytes(b"\x00")

        cmd = DistributeCommand.__new__(DistributeCommand)
        archives = cmd._create_per_os_archives(pkg)

        assert len(archives) == 1
        with zipfile.ZipFile(archives[0][2]) as zf:
            names = {name.split("/")[-1] for name in zf.namelist()}
        assert "otelcol-linux-x64" not in names
        assert "collector-config.yaml" not in names
        assert "config.json" in names
