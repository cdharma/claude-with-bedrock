# ABOUTME: Tests that IDC zero-binary packages do not ship Claude Desktop MDM config
# ABOUTME: Helper-mode desktop auth needs the credential-process binary those packages omit

"""Tests for the Claude Desktop (Cowork 3P) gate in zero-binary packages.

IDC zero-binary mode (auth_type idc, quota disabled) intentionally ships no
binaries — end users authenticate with plain ``aws sso login`` (see
.claude/rules/package-completeness.md). But Claude Desktop's helper-mode MDM
config points at the credential-process binary, so generating it for a
zero-binary package hands users a config referencing a program that is never
installed: Claude Desktop auth silently breaks.

The gate must skip desktop artifacts in zero-binary mode (with an explanation),
NOT add binaries to zero-binary packages.
"""

import ast
import inspect
import textwrap

from claude_code_with_bedrock.cli.commands.package import PackageCommand


def _handle_tree():
    return ast.parse(textwrap.dedent(inspect.getsource(PackageCommand.handle)))


def _enclosing_conditions(tree, target):
    """Branch conditions enclosing ``target``, tracking else-branches too."""
    found = []

    def walk(node, chain):
        for child in ast.iter_child_nodes(node):
            new_chain = chain
            if isinstance(node, ast.If):
                if child in node.body:
                    new_chain = chain + [ast.unparse(node.test)]
                elif child in node.orelse:
                    new_chain = chain + [f"not ({ast.unparse(node.test)})"]
            if child is target:
                found.extend(new_chain)
                return True
            if walk(child, new_chain):
                return True
        return False

    walk(tree, [])
    return found


def _find_call(tree, attr_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr_name:
            return node
    return None


class TestZeroBinaryGate:
    def test_mdm_generation_is_gated_on_zero_binary_mode(self):
        """The regression: generation was guarded only by cowork_3p_enabled,
        so zero-binary packages shipped desktop config pointing at a binary
        that is never built."""
        tree = _handle_tree()
        call = _find_call(tree, "_generate_cowork_3p_mdm_config")
        assert call is not None, "cowork MDM generation call not found in handle()"
        conditions = _enclosing_conditions(tree, call)
        assert any("is_idc_zero_binary" in c and c.startswith("not (") for c in conditions), (
            f"MDM generation must be unreachable in zero-binary mode; enclosing conditions: {conditions}"
        )
        assert any("cowork_3p_enabled" in c for c in conditions), "MDM generation must still require cowork_3p_enabled"

    def test_zero_binary_skip_prints_an_explanation(self):
        """Silent omission would look like a packaging bug; the skip must say
        why and how to get desktop support back (enable quota)."""
        src = textwrap.dedent(inspect.getsource(PackageCommand.handle))
        gate = src.find("is_idc_zero_binary:")
        assert gate != -1
        # Within the skip branch (before the elif), the message must name the
        # product and the remediation.
        skip_branch = src[gate : src.find("elif profile.cowork_3p_enabled", gate)]
        assert "Claude Desktop" in skip_branch, "warning must name Claude Desktop"
        assert "quota" in skip_branch.lower(), "warning must mention the quota remediation"

    def test_summary_only_lists_mdm_files_when_generated(self):
        """The package summary previously keyed on cowork_3p_enabled, so a
        zero-binary package would claim desktop files it never produced."""
        src = textwrap.dedent(inspect.getsource(PackageCommand.handle))
        listing = src.find("cowork-3p-config.json - CoWork 3P MDM configuration")
        assert listing != -1, "summary listing for cowork files not found"
        # The nearest guard above the listing must be the generated flag, not
        # the raw enabled flag.
        window = src[max(0, listing - 400) : listing]
        assert "cowork_mdm_generated" in window, (
            "summary must be gated on cowork_mdm_generated (actually produced), not profile.cowork_3p_enabled"
        )

    def test_binary_idc_package_still_generates_mdm(self):
        """Sanity: the gate must not disable desktop support for IDC WITH
        quota (binaries present) — only the zero-binary combination."""
        tree = _handle_tree()
        call = _find_call(tree, "_generate_cowork_3p_mdm_config")
        conditions = _enclosing_conditions(tree, call)
        # No enclosing condition may require zero-binary mode positively.
        assert not any("is_idc_zero_binary" in c and not c.startswith("not (") for c in conditions), (
            f"MDM generation wrongly requires zero-binary mode: {conditions}"
        )
