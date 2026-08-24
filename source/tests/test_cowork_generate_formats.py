# ABOUTME: cowork generate --format help must list every accepted format
# ABOUTME: ps1 and admx worked but were undocumented, so nobody found the Intune path

"""The option help advertised only 'all, json, mobileconfig, reg' while the
validator accepted admx and ps1 too — the Intune platform script (the
recommended fleet path for per-user attribution) was undiscoverable from the
CLI. Help text is now derived from VALID_FORMATS, so the two cannot drift."""

import inspect

from claude_code_with_bedrock.cli.commands.cowork import VALID_FORMATS, CoworkGenerateCommand


def _format_option():
    for opt in CoworkGenerateCommand.options:
        if opt.name == "format":
            return opt
    raise AssertionError("--format option not found")


def test_help_lists_every_accepted_format():
    description = _format_option().description
    for fmt in VALID_FORMATS:
        assert fmt in description, f"--format help omits '{fmt}', so users cannot discover it"


def test_ps1_and_admx_are_accepted():
    assert "ps1" in VALID_FORMATS and "admx" in VALID_FORMATS


def test_help_explains_the_windows_formats():
    """A format name alone doesn't tell an admin which one Intune wants."""
    description = _format_option().description
    assert "Intune" in description
    assert "Group Policy" in description


def test_validator_uses_the_shared_list():
    src = inspect.getsource(CoworkGenerateCommand.handle)
    assert "VALID_FORMATS" in src
    assert "valid_formats = [" not in src, "a second hardcoded list is how the help went stale"


def test_every_format_reaches_a_generator():
    """A format accepted by the validator must actually produce something."""
    src = inspect.getsource(CoworkGenerateCommand.handle)
    for fmt in VALID_FORMATS:
        if fmt == "all":
            continue
        assert f'"{fmt}"' in src, f"format '{fmt}' is accepted but never dispatched"
