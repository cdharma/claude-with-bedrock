# ABOUTME: The Windows launcher must explain a missing Claude Code install
# ABOUTME: A signed-in customer got a bare "'claude' is not recognized" instead

"""After a successful IDC sign-in, claude-bedrock.cmd ran `claude` unguarded —
a machine without Claude Code got cmd.exe's cryptic 'not recognized' error.
The launcher must check first and say what to install (or that Claude Desktop
users are already done)."""

import inspect

from claude_code_with_bedrock.cli.commands.package import PackageCommand


def _launcher_block():
    src = inspect.getsource(PackageCommand)
    start = src.find("Creating launcher...")
    end = src.find("OK Created launcher", start)
    assert start != -1 and end != -1, "launcher generation block not found"
    return src[start:end]


def test_launcher_guards_missing_claude():
    block = _launcher_block()
    assert "where claude" in block, "launcher must detect a missing claude before running it"
    assert block.index("where claude") < block.index("claude %%*"), "guard must precede the launch"


def test_guard_message_names_the_fix_and_the_desktop_case():
    block = _launcher_block()
    assert "npm install -g @anthropic-ai/claude-code" in block
    assert "Claude Desktop" in block, "Desktop-only users must learn they are already done"
