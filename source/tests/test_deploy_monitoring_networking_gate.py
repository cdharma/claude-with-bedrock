# ABOUTME: Regression tests — monitoring deploy must fail fast when networking outputs are missing
# ABOUTME: Guards .claude/rules/stack-ordering.md: never assume stack outputs exist, fail with clear error

"""Monitoring (otel-collector) deploy must gate on networking stack outputs.

The otel-collector.yaml template requires ``VpcId`` and ``SubnetIds`` (neither
has a Default). In central mode with ``create_vpc`` (the default), those values
come from the networking stack's outputs. Before the fix, the monitoring branch
of ``_deploy_stack`` used ``if networking_outputs:`` with no else and inner
``if vpc_id:`` / ``if subnet_ids:`` guards that silently *skipped* the
parameters — so a missing networking stack sailed on into CloudFormation and
failed there with an opaque "Parameters: [VpcId, SubnetIds] must have values"
error. The sibling distribution branch already checked and returned a clear
"Deploy networking stack first" error; these tests pin the same behavior for
monitoring.

``_deploy_stack`` is shared by the sequential path and the ``--parallel`` wave
path (which calls it with ``parallel=True``), so both are covered here.
"""

import dataclasses
from unittest.mock import MagicMock, patch

from claude_code_with_bedrock.cli.commands.deploy import DeployCommand
from claude_code_with_bedrock.config import Profile

NETWORKING_STACK = "claude-code-test-networking"


def _make_profile(**overrides):
    field_names = {f.name for f in dataclasses.fields(Profile)}
    defaults = {
        "name": "TestProfile",
        "provider_domain": "company.okta.com",
        "client_id": "test-client-id",
        "credential_storage": "session",
        "aws_region": "us-east-1",
        "identity_pool_name": "claude-code-test",
        "sso_enabled": True,
        "provider_type": "okta",
        "monitoring_enabled": True,
        "quota_monitoring_enabled": False,
        # create_vpc defaults to True -> monitoring reads networking outputs
        "monitoring_config": {},
    }
    defaults.update(overrides)
    return Profile(**{k: v for k, v in defaults.items() if k in field_names})


def _console_text(console) -> str:
    return " ".join(str(call.args[0]) for call in console.print.call_args_list if call.args)


def _run_monitoring_deploy(networking_outputs, parallel=False, monitoring_config=None):
    """Invoke _deploy_stack('monitoring', ...) with mocked AWS boundaries."""
    profile = _make_profile(
        monitoring_config={} if monitoring_config is None else monitoring_config,
    )

    mock_manager = MagicMock()
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.outputs = {}
    mock_manager.deploy_stack.return_value = mock_result

    console = MagicMock()

    def fake_get_stack_outputs(stack_name, region):
        if stack_name == NETWORKING_STACK:
            return networking_outputs
        return {}  # monitoring stack post-deploy outputs (irrelevant here)

    command = DeployCommand()
    with (
        patch(
            "claude_code_with_bedrock.cli.commands.deploy.get_stack_outputs",
            side_effect=fake_get_stack_outputs,
        ),
        patch.object(DeployCommand, "_ensure_ecs_service_linked_role"),
        patch("boto3.client", MagicMock()),
    ):
        result = command._deploy_stack("monitoring", profile, console, mock_manager, parallel=parallel)

    return result, mock_manager, console


class TestMonitoringMissingNetworkingStack:
    """Networking stack absent entirely (get_stack_outputs returns {})."""

    def test_returns_nonzero(self):
        result, _, _ = _run_monitoring_deploy(networking_outputs={})
        assert result == 1

    def test_does_not_call_cloudformation(self):
        """The opaque-CFN-failure bug: deploy must never reach CloudFormation."""
        _, manager, _ = _run_monitoring_deploy(networking_outputs={})
        manager.deploy_stack.assert_not_called()

    def test_error_names_the_missing_dependency(self):
        _, _, console = _run_monitoring_deploy(networking_outputs={})
        text = _console_text(console)
        assert "Networking stack outputs not found" in text
        assert "Deploy networking stack first" in text

    def test_parallel_path_also_fails_fast(self):
        """--parallel waves share _deploy_stack; the gate must hold there too."""
        result, manager, console = _run_monitoring_deploy(networking_outputs={}, parallel=True)
        assert result == 1
        manager.deploy_stack.assert_not_called()
        assert "Deploy networking stack first" in _console_text(console)


class TestMonitoringIncompleteNetworkingOutputs:
    """Networking stack exists but lacks the required output keys."""

    def test_missing_subnet_ids_returns_nonzero(self):
        result, manager, console = _run_monitoring_deploy(networking_outputs={"VpcId": "vpc-123"})
        assert result == 1
        manager.deploy_stack.assert_not_called()
        text = _console_text(console)
        assert "Missing required VPC/subnet outputs" in text
        assert "Expected: VpcId, SubnetIds" in text

    def test_missing_vpc_id_returns_nonzero(self):
        result, manager, _ = _run_monitoring_deploy(networking_outputs={"SubnetIds": "subnet-1,subnet-2"})
        assert result == 1
        manager.deploy_stack.assert_not_called()

    def test_empty_string_outputs_treated_as_missing(self):
        result, manager, _ = _run_monitoring_deploy(networking_outputs={"VpcId": "", "SubnetIds": ""})
        assert result == 1
        manager.deploy_stack.assert_not_called()

    def test_error_lists_the_keys_actually_present(self):
        """Mirror the distribution branch: show what was found to aid debugging."""
        _, _, console = _run_monitoring_deploy(networking_outputs={"VpcId": "vpc-123"})
        assert "VpcId" in _console_text(console)


class TestMonitoringWithValidNetworkingOutputs:
    """Happy path must be unchanged: outputs present -> params passed, deploy runs."""

    OUTPUTS = {"VpcId": "vpc-123", "SubnetIds": "subnet-1,subnet-2"}

    def test_deploy_proceeds_and_succeeds(self):
        result, manager, _ = _run_monitoring_deploy(networking_outputs=self.OUTPUTS)
        assert result == 0
        manager.deploy_stack.assert_called_once()

    def test_vpc_parameters_are_passed_to_cloudformation(self):
        _, manager, _ = _run_monitoring_deploy(networking_outputs=self.OUTPUTS)
        params = manager.deploy_stack.call_args.kwargs["parameters"]
        by_key = {p["ParameterKey"]: p["ParameterValue"] for p in params}
        assert by_key["VpcId"] == "vpc-123"
        assert by_key["SubnetIds"] == "subnet-1,subnet-2"


class TestMonitoringExistingVpcModeUnaffected:
    """create_vpc=False (bring-your-own VPC) must not consult the networking stack."""

    def test_existing_vpc_config_bypasses_networking_gate(self):
        result, manager, _ = _run_monitoring_deploy(
            networking_outputs={},  # networking stack absent — must not matter
            monitoring_config={
                "create_vpc": False,
                "vpc_id": "vpc-existing",
                "subnet_ids": ["subnet-a", "subnet-b"],
            },
        )
        assert result == 0
        params = manager.deploy_stack.call_args.kwargs["parameters"]
        by_key = {p["ParameterKey"]: p["ParameterValue"] for p in params}
        assert by_key["VpcId"] == "vpc-existing"
        assert by_key["SubnetIds"] == "subnet-a,subnet-b"
