# ABOUTME: Tests for --parallel wave planning and the CloudFormation waiter outcome enum
# ABOUTME: Waves must never place a stack before the stack whose outputs it reads

"""Tests for parallel deploy wave planning and waiter outcome classification.

`ccwb deploy` deployed all 9-12 stacks strictly sequentially even though the
dependency graph — derived from the cross-stack output reads in _deploy_stack — has
substantial slack:

    monitoring, distribution  <- networking outputs
    quota, bootstrap          <- s3bucket outputs
    analytics, dashboards     <- monitoring outputs
    bootstrap                 <- websearch (gateway URL saved to the profile)

`--parallel` groups them into waves and runs each wave concurrently. The invariant
these tests protect is ordering: if a consumer ever lands in the same wave as its
producer, the consumer reads outputs that do not exist yet and the deploy fails in a
way that is expensive to debug.

Also covers CloudFormationManager._wait_for_stack, which previously returned a bare
bool with every failure branch returning the same value, so a client-side timeout was
indistinguishable from a real stack failure.
"""

from claude_code_with_bedrock.cli.commands.deploy import DeployCommand
from claude_code_with_bedrock.cli.utils.cloudformation import CloudFormationManager

# Consumer -> the stacks whose outputs it reads.
# bootstrap <- websearch is not a CFN output read: the websearch deploy saves
# GatewayMcpEndpoint back to the profile (websearch_gateway_url), which the
# bootstrap params read. If bootstrap ran first on a fresh deploy, it would be
# created without WebSearchGatewayUrl (template default '') until a re-deploy.
DEPENDENCIES = {
    "monitoring": ("networking",),
    "distribution": ("networking",),
    "quota": ("s3bucket",),
    "bootstrap": ("s3bucket", "websearch"),
    "analytics": ("monitoring",),
    "dashboard": ("monitoring",),
    "cowork-dashboard": ("monitoring",),
}

ALL_STACKS = [
    ("auth", "Authentication Stack"),
    ("networking", "VPC Networking"),
    ("s3bucket", "S3 Bucket"),
    ("monitoring", "OpenTelemetry Collector"),
    ("dashboard", "CloudWatch Dashboard"),
    ("cowork-dashboard", "CoWork Dashboard"),
    ("analytics", "Analytics Pipeline"),
    ("quota", "Quota Monitoring"),
    ("distribution", "Distribution infrastructure"),
    ("codebuild", "CodeBuild"),
    ("bootstrap", "Bootstrap Server"),
    ("websearch", "AgentCore Gateway"),
]


def _wave_index(waves, stack_type):
    for i, wave in enumerate(waves):
        if any(st == stack_type for st, _ in wave):
            return i
    return None


class TestWaveOrdering:
    def test_every_consumer_lands_after_its_producer(self):
        """The core invariant: never deploy a stack before the outputs it reads."""
        waves = DeployCommand._plan_waves(ALL_STACKS)
        for consumer, producers in DEPENDENCIES.items():
            for producer in producers:
                ci, pi = _wave_index(waves, consumer), _wave_index(waves, producer)
                assert ci is not None and pi is not None
                assert pi < ci, f"{consumer} (wave {ci}) must come after {producer} (wave {pi})"

    def test_producer_and_consumer_never_share_a_wave(self):
        """Same wave means concurrent, which for a dependency means a race."""
        waves = DeployCommand._plan_waves(ALL_STACKS)
        for consumer, producers in DEPENDENCIES.items():
            for producer in producers:
                assert _wave_index(waves, consumer) != _wave_index(waves, producer)

    def test_all_selected_stacks_are_planned_exactly_once(self):
        waves = DeployCommand._plan_waves(ALL_STACKS)
        planned = [st for wave in waves for st, _ in wave]
        assert sorted(planned) == sorted(st for st, _ in ALL_STACKS)
        assert len(planned) == len(set(planned)), "a stack was scheduled twice"

    def test_first_wave_is_genuinely_independent(self):
        waves = DeployCommand._plan_waves(ALL_STACKS)
        for stack_type, _ in waves[0]:
            assert stack_type not in DEPENDENCIES, f"{stack_type} reads another stack's outputs"

    def test_partial_selection_stays_valid(self):
        """Feature flags mean most deploys are a subset; ordering must still hold."""
        subset = [s for s in ALL_STACKS if s[0] in ("auth", "networking", "monitoring", "analytics")]
        waves = DeployCommand._plan_waves(subset)
        assert _wave_index(waves, "networking") < _wave_index(waves, "monitoring")
        assert _wave_index(waves, "monitoring") < _wave_index(waves, "analytics")

    def test_empty_selection_produces_no_waves(self):
        assert DeployCommand._plan_waves([]) == []

    def test_unknown_stack_gets_its_own_trailing_wave(self):
        """An unlisted stack must not be assumed independent."""
        waves = DeployCommand._plan_waves([("auth", "Auth"), ("brand-new", "Something New")])
        assert _wave_index(waves, "brand-new") == len(waves) - 1
        assert waves[-1] == [("brand-new", "Something New")]

    def test_parallelism_actually_reduces_step_count(self):
        """If waves were no better than sequential there would be no point."""
        waves = DeployCommand._plan_waves(ALL_STACKS)
        assert len(waves) < len(ALL_STACKS)

    def test_poll_delay_backs_off_for_concurrency(self):
        """Concurrent waiters multiply describe_stack_events calls, which throttles."""
        assert DeployCommand.PARALLEL_POLL_DELAY > CloudFormationManager.POLL_DELAY_SECONDS


class TestWaiterOutcomes:
    def test_outcomes_are_distinct(self):
        """The regression: all failure branches used to collapse to one value."""
        outcomes = {
            CloudFormationManager.WAIT_SUCCESS,
            CloudFormationManager.WAIT_FAILED,
            CloudFormationManager.WAIT_ROLLBACK,
            CloudFormationManager.WAIT_TIMEOUT,
            CloudFormationManager.WAIT_GONE,
        }
        assert len(outcomes) == 5

    def test_timeout_is_not_failure(self):
        assert CloudFormationManager.WAIT_TIMEOUT != CloudFormationManager.WAIT_FAILED


class TestNullProgress:
    def test_supports_the_progress_surface_used_by_deploy(self):
        from claude_code_with_bedrock.cli.commands.deploy import _NullProgress

        with _NullProgress() as p:
            task = p.add_task("x", total=None)
            p.update(task, description="y")
            p.update(task, completed=True)

    def test_does_not_swallow_exceptions(self):
        """__exit__ must return falsy or real deploy errors vanish."""
        from claude_code_with_bedrock.cli.commands.deploy import _NullProgress

        assert not _NullProgress().__exit__(ValueError, ValueError("boom"), None)
