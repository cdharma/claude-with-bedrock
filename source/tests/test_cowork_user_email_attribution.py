# ABOUTME: Per-user Desktop telemetry — x-user-email travels via otlpHeaders
# ABOUTME: Without it, Claude Desktop events have no user_email and every per-user widget is empty

"""Claude Desktop has no otel-helper, so per-user dashboard attribution rides
an x-user-email OTLP header: baked per-device via `cowork generate
--user-email`, or a __CCWB_USER_EMAIL__ placeholder in packaged MDM files that
install.bat / install.sh substitute (empty answer -> "unknown"). The central
collector mirrors the header into the snake_case user_email attribute the
dashboard's metric filters read, defaulting to "unknown" so Cost-by-Model
works even for unattributed traffic."""

import inspect
import json
from pathlib import Path

from claude_code_with_bedrock.cli.utils.cowork_3p import _otlp_headers

TEMPLATE = Path(__file__).parent.parent.parent / "deployment" / "infrastructure" / "otel-collector.yaml"


class TestOtlpHeaders:
    def test_email_and_token(self):
        assert _otlp_headers("tok", "a@b.com") == {"X-Cowork-Token": "tok", "x-user-email": "a@b.com"}

    def test_email_without_token_still_sends_headers(self):
        """The regression: headers were only emitted when a service token existed."""
        assert _otlp_headers(None, "a@b.com") == {"x-user-email": "a@b.com"}

    def test_token_only_unchanged(self):
        assert _otlp_headers("tok", None) == {"X-Cowork-Token": "tok"}

    def test_neither_is_empty(self):
        assert _otlp_headers(None, None) == {}


class TestPackagedPlaceholder:
    def test_package_requests_the_placeholder(self):
        from claude_code_with_bedrock.cli.commands.package import PackageCommand

        src = inspect.getsource(PackageCommand)
        assert 'user_email="__CCWB_USER_EMAIL__"' in src

    def test_install_bat_prompts_and_defaults_to_unknown(self):
        from claude_code_with_bedrock.cli.commands import package

        src = inspect.getsource(package)
        bat = src[src.find('if exist "cowork-3p.reg"') :]
        bat = bat[: bat.find("cowork-credential-helper.cmd")]
        assert "set /p CCWB_USER_EMAIL=" in bat, "installer must ask the user once"
        assert 'set "CCWB_USER_EMAIL=unknown"' in bat, "empty answer must become unknown, never a literal placeholder"
        assert "__CCWB_USER_EMAIL__" in bat, "the .reg substitution must cover the email placeholder"

    def test_install_sh_prompts_and_defaults_to_unknown(self):
        from claude_code_with_bedrock.cli.commands import package

        src = inspect.getsource(package)
        sh = src[src.find("Per-user dashboard attribution: Claude Desktop has no otel-helper") :]
        sh = sh[: sh.find("Resolved home directory")]
        assert "read -r _ccwb_email" in sh
        assert '_ccwb_email="unknown"' in sh
        assert "s|__CCWB_USER_EMAIL__|$_ccwb_email|g" in sh

    def test_generate_exposes_user_email_option(self):
        from claude_code_with_bedrock.cli.commands import cowork

        src = inspect.getsource(cowork)
        assert '"user-email"' in src
        assert 'user_email=self.option("user-email")' in src


class TestCollectorDefaults:
    def test_both_config_variants_mirror_header_to_snake_case(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        assert text.count("- key: user_email") == 4, "each of the 2 config blocks needs upsert + insert"
        # upsert from the header, in both blocks
        assert text.count("key: user_email\n                    from_context: metadata.x-user-email") == 2

    def test_unknown_default_uses_insert_not_upsert(self):
        """insert only fills the gap — a real header (or native attribute) must win."""
        text = TEMPLATE.read_text(encoding="utf-8")
        blocks = text.split('- key: user_email\n                    value: "unknown"')
        assert len(blocks) == 3, "both config blocks need the unknown default"
        for tail in blocks[1:]:
            assert tail.lstrip().startswith("action: insert"), "default must be insert (fill-if-absent)"


class TestMdmConfigJsonStaysValid:
    def test_headers_serialise_to_valid_json(self):
        headers = _otlp_headers("tok", "__CCWB_USER_EMAIL__")
        parsed = json.loads(json.dumps(headers))
        assert parsed["x-user-email"] == "__CCWB_USER_EMAIL__"
