# ABOUTME: Regression test — quota limit prompts must be reachable on the IDC auth path
# ABOUTME: They were nested inside the OIDC-only else branch, so IDC silently kept defaults

"""Ensures `ccwb init` asks for quota limits on every auth mode that supports quota.

The quota section branches three ways on auth type:

    if config.get("auth_type") == "none":     # cannot enforce, skip
    elif config.get("auth_type") == "idc":    # asks "Enable quota enforcement?"
    else:                                     # OIDC

The limit-type, budget and enforcement prompts were indented *inside* the ``else``
branch. On the IDC path the wizard printed "Configure quota limits and thresholds"
and then jumped straight to the next feature, asking nothing. The profile kept
whatever defaults existed — token-based, 300,000,000 tokens, ``block`` — regardless
of what the admin selected.

That was silent and consequential: with working quota enforcement, a 300M-token cap
in block mode is roughly $600-1500 of Sonnet 5 spend rather than the budget the admin
asked for.

These tests are structural because the prompts sit inside a long interactive
method that cannot be exercised without a TTY. They assert reachability, which is
exactly what regressed.
"""

import ast
import inspect
import textwrap

from claude_code_with_bedrock.cli.commands.init import InitCommand


def _is_questionary_call(node):
    """True for questionary.select(...) / .text(...) / .confirm(...) and friends.

    Must exclude console.print: the 'none' auth branch prints an explanation
    mentioning "enforcement", which is not a prompt and is correctly auth-gated.
    """
    func = node.func
    while isinstance(func, ast.Attribute):
        func = func.value
    return isinstance(func, ast.Name) and func.id == "questionary"


def _quota_prompt_nodes(tree):
    """Every questionary prompt that configures a quota limit or enforcement mode."""
    # Only the prompts that configure the limits themselves. Deliberately excludes
    # "Enable quota enforcement?" — gating that toggle on auth type is correct, since
    # the IDC branch carries its own credential-process caveat — and the unrelated
    # settings-target choice whose label also contains "enforcement".
    wanted = (
        "how do you want to limit usage",
        "budget per user",
        "token limit per user",
        "limit enforcement:",
    )
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_questionary_call(node):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if any(w in arg.value.lower() for w in wanted):
                    found.append((node, arg.value))
    return found


def _enclosing_conditions(tree, target):
    """Every branch condition that encloses ``target``, as source strings.

    Tracks ``orelse`` as well as ``body``. The original bug nested the prompts in an
    ``else`` branch, so a walker that only followed ``body`` would report them as
    unconditional and miss the defect entirely.
    """
    enclosing = []

    def walk(node, chain):
        for child in ast.iter_child_nodes(node):
            new_chain = chain
            if isinstance(node, ast.If):
                if child in node.body:
                    new_chain = chain + [ast.unparse(node.test)]
                elif child in node.orelse:
                    new_chain = chain + [f"not ({ast.unparse(node.test)})"]
            if child is target:
                enclosing.extend(new_chain)
                return True
            if walk(child, new_chain):
                return True
        return False

    walk(tree, [])
    return enclosing


class TestQuotaPromptsReachableOnIdc:
    def setup_method(self):
        src = textwrap.dedent(inspect.getsource(InitCommand._gather_configuration))
        self.tree = ast.parse(src)
        self.prompts = _quota_prompt_nodes(self.tree)

    def test_quota_prompts_exist_at_all(self):
        assert self.prompts, "no quota limit prompts found — has the section been renamed?"

    def test_no_quota_prompt_is_gated_on_auth_type(self):
        """The regression: prompts nested under an auth-type comparison are only
        reachable for that one auth mode."""
        for node, label in self.prompts:
            for cond in _enclosing_conditions(self.tree, node):
                assert "auth_type" not in cond, (
                    f"quota prompt {label!r} is gated on {cond!r}; it will be skipped for every other auth mode"
                )

    def test_limit_type_prompt_is_guarded_only_by_the_enabled_flag(self):
        """It should run whenever quota is enabled, irrespective of auth mode."""
        limit_prompts = [(n, lbl) for n, lbl in self.prompts if "limit usage" in lbl.lower()]
        assert limit_prompts, "the limit-type prompt disappeared"
        for node, _ in limit_prompts:
            guards = _enclosing_conditions(self.tree, node)
            assert any("enable_quota_monitoring" in g for g in guards), (
                f"limit-type prompt is not guarded by enable_quota_monitoring; guards were {guards}"
            )

    def test_enable_quota_monitoring_is_initialised_before_the_branch(self):
        """The 'none' auth branch never prompts, so the guard needs a seeded value or
        it raises UnboundLocalError."""
        src = inspect.getsource(InitCommand._gather_configuration)
        assign = src.find("enable_quota_monitoring = False")
        first_branch = src.find('config.get("auth_type") == "none"')
        assert assign != -1, "enable_quota_monitoring is never given a default"
        assert assign < first_branch, "default must be set before the auth-type branch"
