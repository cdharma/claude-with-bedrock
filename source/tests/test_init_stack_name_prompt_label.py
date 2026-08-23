# ABOUTME: Regression test — IDC must not be asked for a Cognito "Identity Pool Name"
# ABOUTME: No Cognito pool exists on IDC; the value only seeds CloudFormation stack names

"""The infrastructure step asks for a base name that seeds every stack name.

On the OIDC/Cognito path a real Cognito Identity Pool is created with that
name, so the label "Identity Pool Name:" is accurate. On IDC (and Direct STS)
no Cognito resource exists — bedrock-auth-idc.yaml contains zero Cognito
resources — so the honest label is "Stack base name (for CloudFormation):".

The branch used to test only ``federation_type == "direct"``, and IDC profiles
carry the legacy default ``federation_type: "cognito"``, so IDC admins were
asked for an identity pool that would never exist.
"""

import ast
import inspect
import textwrap

from claude_code_with_bedrock.cli.commands.init import InitCommand


def _prompt_gate_test():
    """Return the unparsed condition of the if-statement that selects between
    the stack-base-name label and the identity-pool label."""
    src = textwrap.dedent(inspect.getsource(InitCommand._gather_configuration))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not node.orelse:
            continue
        segment = ast.unparse(node)
        if "Stack base name" in segment and "Identity Pool Name" in segment:
            return ast.unparse(node.test)
    return None


class TestStackNamePromptLabel:
    def test_the_label_branch_exists(self):
        assert _prompt_gate_test() is not None, (
            "could not find the if/else choosing between the stack-base-name "
            "and identity-pool prompt labels — has the prompt been restructured?"
        )

    def test_idc_reaches_the_honest_label(self):
        """The regression: the gate only checked federation_type == 'direct',
        and IDC profiles default to federation_type 'cognito'."""
        gate = _prompt_gate_test()
        assert "auth_type" in gate and "idc" in gate, (
            f"the label gate {gate!r} does not consider auth_type, so IDC "
            "admins are asked for a Cognito Identity Pool that never exists"
        )

    def test_idc_template_creates_no_cognito_resources(self):
        """The premise of the label fix: IDC provisions no Cognito resources."""
        from pathlib import Path

        template = Path(__file__).parent.parent.parent / "deployment" / "infrastructure" / "bedrock-auth-idc.yaml"
        assert "Cognito" not in template.read_text(encoding="utf-8")
