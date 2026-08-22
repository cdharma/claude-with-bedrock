# ABOUTME: Regression tests for per-stack deletion timeouts in ccwb destroy
# ABOUTME: A 5-minute budget reported spurious failures on the monitoring stack

"""Tests for DestroyCommand deletion timeouts.

``destroy`` waited only 300 seconds per stack, overriding the 600-second default in
CloudFormationManager.delete_stack. The monitoring stack has to drain an ECS service,
delete an Application Load Balancer and wait for ENI detachment, which regularly runs
past that. On timeout ``StackDeletionResult.success`` is False and ``.error`` is None,
so the command printed:

    Error deleting stack: None
    ⚠ Monitoring stack has resources requiring manual cleanup

…while CloudFormation completed the deletion normally moments later. The stack was
gone; only the report was wrong.

That mattered beyond cosmetics: it invited the operator to hand-delete resources that
were still mid-deletion, which is how a healthy delete becomes a genuinely stuck one.
"""

from claude_code_with_bedrock.cli.commands.destroy import DESTROYABLE_STACKS, DestroyCommand


class TestPerStackDeleteTimeouts:
    def test_monitoring_gets_more_than_the_old_five_minutes(self):
        """The regression: 300s was not enough for ECS drain plus ALB deletion."""
        timeout = DestroyCommand._delete_timeout("claude-code-auth-monitoring")
        assert timeout > 300, "monitoring still uses a budget known to be too short"
        assert timeout >= 900, f"expected a generous budget for ECS + ALB, got {timeout}s"

    def test_networking_allows_for_nat_gateway_deletion(self):
        """NAT gateway deletion alone routinely takes several minutes."""
        assert DestroyCommand._delete_timeout("claude-code-auth-networking") > 600

    def test_default_is_not_below_the_library_default(self):
        """destroy.py must not silently shorten CloudFormationManager's own default."""
        import inspect

        from claude_code_with_bedrock.cli.utils.cloudformation import CloudFormationManager

        lib_default = inspect.signature(CloudFormationManager.delete_stack).parameters["timeout"].default
        assert DestroyCommand.STACK_DELETE_TIMEOUT_DEFAULT >= lib_default

    def test_unknown_stack_falls_back_to_default(self):
        assert DestroyCommand._delete_timeout("some-unrelated-stack") == DestroyCommand.STACK_DELETE_TIMEOUT_DEFAULT

    def test_suffix_match_is_anchored(self):
        """'monitoring' must match the suffix, not appear anywhere in the name."""
        assert (
            DestroyCommand._delete_timeout("monitoring-something-else") == DestroyCommand.STACK_DELETE_TIMEOUT_DEFAULT
        )

    def test_every_configured_suffix_is_a_real_stack(self):
        """A typo in the timeout map would silently apply the default instead."""
        for suffix in DestroyCommand.STACK_DELETE_TIMEOUTS:
            assert suffix in DESTROYABLE_STACKS, f"{suffix!r} is not a destroyable stack"

    def test_all_timeouts_resolve_for_real_stack_names(self):
        """Every destroyable stack must produce a positive budget."""
        for stack in DESTROYABLE_STACKS:
            assert DestroyCommand._delete_timeout(f"claude-code-auth-{stack}") > 0


class TestReturnCodeContract:
    def test_timeout_code_is_documented_and_distinct(self):
        """3 means 'still deleting', which is not the same as 1 (DELETE_FAILED) or
        2 (real error). Conflating them is what produced the misleading advice."""
        doc = DestroyCommand._delete_stack.__doc__
        assert doc is not None
        for code in ("0:", "1:", "2:", "3:"):
            assert code in doc, f"return code {code} is undocumented"

    def test_never_reports_a_bare_none_error(self):
        """result.error is None on timeout; the message must not print it raw."""
        import inspect

        src = inspect.getsource(DestroyCommand._delete_stack)
        assert "result.error or" in src, "error message can still render a bare None"
