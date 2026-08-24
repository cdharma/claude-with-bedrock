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

    def test_install_bat_resolves_email_non_interactively(self):
        """A silent (Intune) install must never block on input, so detection
        happens inside the existing PowerShell call — no prompt, no batch
        escaping."""
        from claude_code_with_bedrock.cli.commands import package

        src = inspect.getsource(package)
        bat = src[src.find('if exist "cowork-3p.reg"') :]
        bat = bat[: bat.find("cowork-credential-helper.cmd")]
        assert "set /p" not in bat, "no interactive prompt — it would hang a silent install"
        assert "$env:CCWB_USER_EMAIL" in bat, "an explicit env var must win"
        assert "whoami /upn" in bat, "Entra UPN is the automatic source"
        assert "'unknown'" in bat, "must fall back rather than ship a placeholder"
        assert "__CCWB_USER_EMAIL__" in bat, "the .reg substitution must cover the email placeholder"

    def test_install_bat_email_precedence_env_then_upn(self):
        """Compare inside the PowerShell command itself — the REM comment above
        it names both sources, so a whole-block index comparison is meaningless."""
        from claude_code_with_bedrock.cli.commands import package

        src = inspect.getsource(package)
        line = next(line for line in src.splitlines() if "powershell -NoProfile" in line and "cowork-3p.reg" in line)
        assert line.index("$env:CCWB_USER_EMAIL") < line.index("whoami /upn")

    def test_install_bat_rejects_a_non_email_value(self):
        """A machine-local account name is not an email; it must not become a
        phantom dashboard user."""
        from claude_code_with_bedrock.cli.commands import package

        src = inspect.getsource(package)
        assert "-notmatch '@'" in src

    def test_install_sh_resolves_email_non_interactively(self):
        from claude_code_with_bedrock.cli.commands import package

        src = inspect.getsource(package)
        sh = src[src.find("Per-user dashboard attribution: Claude Desktop has no otel-helper") :]
        sh = sh[: sh.find("Resolved home directory")]
        assert "read -r" not in sh, "no prompt — MDM installs run unattended"
        assert "CCWB_USER_EMAIL" in sh, "explicit env var override"
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
        assert text.count("- key: user_email") == 6, "each of the 2 config blocks needs upsert + 2 inserts"
        # upsert from the header, in both blocks
        assert text.count("key: user_email\n                    from_context: metadata.x-user-email") == 2

    def test_enduser_id_fallback_precedes_unknown(self):
        """Real Claude Desktop events carry identity at attributes.enduser.id and
        no user_email at all — the dimension then fails to resolve and CloudWatch
        publishes no datapoint, emptying every per-user and per-model widget."""
        text = TEMPLATE.read_text(encoding="utf-8")
        assert text.count("from_attribute: enduser.id") == 2, "both config variants need the fallback"
        for block in text.split("- key: user_email\n                    from_attribute: enduser.id")[1:]:
            nxt = block.find('value: "unknown"')
            assert nxt != -1 and nxt < 400, "the unknown default must follow the enduser.id fallback"

    def test_fallbacks_are_insert_so_the_header_wins(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        for marker in ("from_attribute: enduser.id", 'value: "unknown"'):
            for block in text.split(marker)[1:]:
                assert block.lstrip().startswith("action: insert"), f"{marker} must be insert (fill-if-absent)"

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


class TestAutomaticIdentity:
    """Fleet rollout must not depend on users typing their own email."""

    def test_intune_script_resolves_upn_at_deploy_time(self):
        import tempfile

        from claude_code_with_bedrock.cli.utils.cowork_3p import build_mdm_config, generate_intune_script

        cfg = build_mdm_config("ap-south-1", ["sonnet"])
        cfg["otlpHeaders"] = json.dumps({"X-Cowork-Token": "tok"})
        with tempfile.TemporaryDirectory() as d:
            ps1 = generate_intune_script(Path(d), cfg)
            text = ps1.read_text(encoding="utf-8")
        assert "whoami /upn" in text
        assert "$ccwbUserEmail = 'unknown'" in text, "must fall back, not ship a placeholder"
        assert ".Replace('__CCWB_USER_EMAIL__', $ccwbUserEmail)" in text, "header value must be substituted"

    def test_intune_script_injects_header_even_without_user_email_option(self):
        """IT should get per-user attribution from the ps1 path with no extra flags."""
        import tempfile

        from claude_code_with_bedrock.cli.utils.cowork_3p import build_mdm_config, generate_intune_script

        cfg = build_mdm_config("ap-south-1", ["sonnet"])
        cfg["otlpHeaders"] = json.dumps({"X-Cowork-Token": "tok"})
        with tempfile.TemporaryDirectory() as d:
            text = generate_intune_script(Path(d), cfg).read_text(encoding="utf-8")
        assert "x-user-email" in text

    def test_explicit_user_email_is_not_overwritten(self):
        import tempfile

        from claude_code_with_bedrock.cli.utils.cowork_3p import build_mdm_config, generate_intune_script

        cfg = build_mdm_config("ap-south-1", ["sonnet"])
        cfg["otlpHeaders"] = json.dumps({"x-user-email": "real@example.com"})
        with tempfile.TemporaryDirectory() as d:
            text = generate_intune_script(Path(d), cfg).read_text(encoding="utf-8")
        assert "real@example.com" in text
        assert "__CCWB_USER_EMAIL__" not in text
