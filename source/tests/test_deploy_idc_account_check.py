# ABOUTME: Tests the IDC deploy account guard — federated role must land in idc_account_id
# ABOUTME: Deploying with the wrong AWS profile used to exit 0 with an unusable role

"""Tests for the pre-deploy IDC account check.

The IDC auth stack creates the federated role that Identity Center users
assume. Deploying it with credentials for a different account than
``profile.idc_account_id`` used to succeed (exit 0) while producing a role no
IDC user could ever assume — the failure only surfaced at end-user login,
hours later and in the wrong account. deploy.py contained no STS call at all.
"""

import ast
import inspect
import io
import textwrap
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from claude_code_with_bedrock.cli.commands.deploy import DeployCommand, _check_idc_account_match


def _profile(**kw):
    defaults = {"idc_account_id": "535837038761", "aws_region": "ap-south-1"}
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _console():
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=200), buf


class TestAccountMismatch:
    def test_mismatch_blocks_the_deploy(self):
        console, buf = _console()
        with patch("boto3.client") as client:
            client.return_value.get_caller_identity.return_value = {"Account": "111111111111"}
            assert _check_idc_account_match(_profile(), console) is False
        out = buf.getvalue()
        assert "111111111111" in out, "error must name the caller account"
        assert "535837038761" in out, "error must name the expected IDC account"

    def test_match_allows_the_deploy(self):
        console, _ = _console()
        with patch("boto3.client") as client:
            client.return_value.get_caller_identity.return_value = {"Account": "535837038761"}
            assert _check_idc_account_match(_profile(), console) is True

    def test_missing_idc_account_skips_check_without_calling_sts(self):
        """Older profiles have no idc_account_id — backward compat, no STS call."""
        console, _ = _console()
        with patch("boto3.client") as client:
            assert _check_idc_account_match(_profile(idc_account_id=None), console) is True
            assert _check_idc_account_match(_profile(idc_account_id=""), console) is True
            client.assert_not_called()

    def test_sts_errors_propagate_not_swallowed(self):
        """A credential failure must surface through the existing error path,
        not be silently treated as a pass or a mismatch."""
        console, _ = _console()
        with patch("boto3.client") as client:
            client.return_value.get_caller_identity.side_effect = RuntimeError("ExpiredToken")
            try:
                _check_idc_account_match(_profile(), console)
            except RuntimeError:
                pass
            else:
                raise AssertionError("STS error was swallowed")

    def test_account_compared_as_string(self):
        """STS returns the account as a string; the profile may hold either."""
        console, _ = _console()
        with patch("boto3.client") as client:
            client.return_value.get_caller_identity.return_value = {"Account": "535837038761"}
            assert _check_idc_account_match(_profile(idc_account_id=535837038761), console) is True


class TestGuardIsWiredIntoTheIdcDeployPath:
    def test_check_runs_before_the_idc_template_is_deployed(self):
        """The regression: the guard must gate the IDC auth-stack branch.

        Structural check — inside _deploy_stack, the account check must appear
        in the IDC branch before the bedrock-auth-idc template is used.
        """
        src = textwrap.dedent(inspect.getsource(DeployCommand._deploy_stack))
        check_pos = src.find("_check_idc_account_match")
        template_pos = src.find("bedrock-auth-idc.yaml")
        assert check_pos != -1, "the IDC deploy path no longer calls _check_idc_account_match"
        assert template_pos != -1, "could not locate the IDC template reference"
        assert check_pos < template_pos, "account check must run before the IDC template is deployed"

    def test_failed_check_returns_nonzero(self):
        """Exit-code contract: a blocked deploy is a failure, not a skip."""
        src = textwrap.dedent(inspect.getsource(DeployCommand._deploy_stack))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.UnaryOp)
                and "_check_idc_account_match" in ast.unparse(node.test)
            ):
                returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
                assert returns, "guard must return on mismatch"
                assert any(ast.unparse(r.value) == "1" for r in returns if r.value is not None), (
                    "guard must return 1 (non-zero) on mismatch"
                )
                return
        raise AssertionError("could not find the `if not _check_idc_account_match(...)` guard")
