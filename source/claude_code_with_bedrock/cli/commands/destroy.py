# ABOUTME: Destroy command for cleaning up AWS resources
# ABOUTME: Safely removes deployed stacks and configurations

"""Destroy command - Remove deployed infrastructure."""

from cleo.commands.command import Command
from cleo.helpers import argument, option
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm

from claude_code_with_bedrock.cli.utils.cloudformation import CloudFormationManager
from claude_code_with_bedrock.cli.utils.helpers import clear_cached_credentials, get_codebuild_region
from claude_code_with_bedrock.config import WEBSEARCH_SUPPORTED_REGIONS, Config

# All destroyable stacks in reverse dependency order (destroy-all uses this sequence).
# Leaf stacks (no dependents) go first; foundational stacks (auth) go last.
# New stacks: add before 'codebuild' if they have no cross-stack dependencies.
# Keep in sync with VALID_STACKS in deploy.py.
DESTROYABLE_STACKS = [
    "bootstrap",  # Leaf: device-code/OIDC bootstrap server, no dependents
    "websearch",  # Leaf: AgentCore gateway, no dependents, may be cross-region
    "codebuild",  # Leaf: build pipeline, may be cross-region
    "analytics",
    "quota",
    "cowork-dashboard",
    "dashboard",
    "monitoring",
    "distribution",
    "networking",
    "s3bucket",
    "auth",  # Foundation: destroy last (other stacks reference its outputs)
]


class DestroyCommand(Command):
    name = "destroy"
    description = "Remove deployed AWS infrastructure"

    arguments = [
        argument(
            "stack",
            description=f"Specific stack to destroy ({'/'.join(DESTROYABLE_STACKS)})",
            optional=True,
        )
    ]

    options = [
        option("profile", description="Configuration profile to use", flag=False),
        option("force", description="Skip confirmation prompts", flag=True),
    ]

    def handle(self) -> int:
        """Execute the destroy command."""
        console = Console()

        # Load configuration
        config = Config.load()

        # Get profile name (use active profile if not specified)
        profile_name = self.option("profile")
        if not profile_name:
            profile_name = config.active_profile
            console.print(f"[dim]Using active profile: {profile_name}[/dim]\n")
        else:
            console.print(f"[dim]Using profile: {profile_name}[/dim]\n")

        profile = config.get_profile(profile_name)

        if not profile:
            if profile_name:
                console.print(f"[red]Profile '{profile_name}' not found. Run 'poetry run ccwb init' first.[/red]")
            else:
                console.print(
                    "[red]No active profile set. Run 'poetry run ccwb init' or "
                    "'poetry run ccwb context use <profile>' first.[/red]"
                )
            return 1

        # Determine which stacks to destroy
        stack_arg = self.argument("stack")
        force = self.option("force")

        stacks_to_destroy = []
        if stack_arg:
            if stack_arg in DESTROYABLE_STACKS:
                stacks_to_destroy.append(stack_arg)
            else:
                console.print(f"[red]Unknown stack: {stack_arg}[/red]")
                console.print(f"Valid stacks: {', '.join(DESTROYABLE_STACKS)}")
                return 1
        else:
            # Destroy all stacks in reverse dependency order
            stacks_to_destroy = list(DESTROYABLE_STACKS)

        # Show what will be destroyed
        console.print(
            Panel.fit(
                "[bold red]⚠️  Infrastructure Destruction Warning[/bold red]\n\n"
                "This will permanently delete the following AWS resources:",
                border_style="red",
                padding=(1, 2),
            )
        )

        for stack in stacks_to_destroy:
            stack_name = profile.stack_names.get(stack, f"{profile.identity_pool_name}-{stack}")
            console.print(f"• {stack.capitalize()} stack: [cyan]{stack_name}[/cyan]")

        console.print("\n[yellow]Note: Some resources may require manual cleanup:[/yellow]")
        console.print("• CloudWatch LogGroups (/ecs/otel-collector, /aws/claude-code/metrics)")
        console.print("• S3 Buckets and Athena resources created by analytics stack")
        console.print("• Any custom resources created outside of CloudFormation")

        # Confirm destruction
        if not force:
            if not Confirm.ask("\n[bold red]Are you sure you want to destroy these resources?[/bold red]"):
                console.print("\n[yellow]Destruction cancelled.[/yellow]")
                return 0

        # Destroy stacks
        console.print("\n[bold]Destroying stacks...[/bold]\n")

        all_failed_resources = []  # Collect failed resources from all stacks
        all_retained_resources = []  # Collect intentionally retained resources
        stacks_with_failures = []

        for stack in stacks_to_destroy:
            if stack == "monitoring" and not profile.monitoring_enabled:
                continue
            if stack == "dashboard" and not profile.monitoring_enabled:
                continue
            if stack == "networking" and not profile.monitoring_enabled:
                continue
            if stack == "analytics" and not profile.monitoring_enabled:
                continue
            if stack == "s3bucket" and not profile.monitoring_enabled:
                continue
            # Skip ECS-related stacks in sidecar mode
            monitoring_mode = getattr(profile, "monitoring_mode", "central")
            if monitoring_mode == "sidecar" and stack in ("networking", "monitoring", "analytics", "s3bucket"):
                continue
            if stack == "quota" and not getattr(profile, "quota_monitoring_enabled", False):
                continue
            if stack == "distribution" and not getattr(profile, "enable_distribution", False):
                continue
            if stack == "codebuild" and not getattr(profile, "enable_codebuild", False):
                continue
            if stack == "websearch" and not getattr(profile, "web_search_enabled", False):
                continue

            stack_name = profile.stack_names.get(stack, f"{profile.identity_pool_name}-{stack}")
            # CodeBuild may have been deployed cross-region (Windows container fleet
            # isn't in every region); delete it where it actually lives, or it's
            # silently orphaned in the build region while the destroy reports success.
            # Web search likewise deploys into us-east-1 (managed connector region).
            if stack == "codebuild":
                stack_region = get_codebuild_region(profile)
            elif stack == "websearch":
                stack_region = getattr(profile, "websearch_region", None) or WEBSEARCH_SUPPORTED_REGIONS[0]
            else:
                stack_region = profile.aws_region
            console.print(f"Destroying {stack} stack: [cyan]{stack_name}[/cyan]")

            result = self._delete_stack(stack_name, stack_region, console)
            if result != 0:
                # Don't break - record the failure and continue with remaining stacks.
                # Always track the stack: a non-zero result means it did not delete cleanly.
                # Enumerable DELETE_FAILED resources may be empty (e.g. a real delete error,
                # or a client-side timeout while resources are still DELETE_IN_PROGRESS), and
                # the summary must not report overall success in that case.
                stacks_with_failures.append(stack_name)
                failed = self._get_failed_resources(stack_name, stack_region)
                if failed:
                    all_failed_resources.extend(failed)
                    console.print(f"[yellow]⚠ {stack.capitalize()} stack — failed resources:[/yellow]")
                    for r in failed:
                        console.print(f"    • {r['logical_id']} ({r['resource_type']}): {r['physical_id']}")
                else:
                    console.print(
                        f"[yellow]⚠ {stack.capitalize()} stack has resources requiring manual cleanup[/yellow]"
                    )
                console.print()
            else:
                # Check for silently retained resources (DeletionPolicy: Retain)
                retained = self._get_retained_resources(stack_name, stack_region)
                if retained:
                    all_retained_resources.extend(retained)
                    console.print(f"[yellow]ℹ {stack.capitalize()} stack — retained resources (by policy):[/yellow]")
                    for r in retained:
                        console.print(f"    • {r['logical_id']} ({r['resource_type']}): {r['physical_id']}")
                    console.print()
                else:
                    console.print(f"[green]✓ {stack.capitalize()} stack destroyed[/green]\n")

        # Clean up CodeBuild stacks left in regions the build region was moved away
        # from (cross-region was reconfigured via re-init). Without this they're
        # orphaned: the loop above only deletes in the *current* codebuild region.
        # NOT gated on enable_codebuild — disabling CodeBuild (init "Skip") is
        # exactly when an old cross-region stack needs cleaning, and the main loop
        # skips codebuild when it's disabled. prior_regions is only populated when
        # there's an orphan to clean, so iterating it unconditionally is safe.
        current_cb_region = get_codebuild_region(profile)
        cb_stack_name = profile.stack_names.get("codebuild", f"{profile.identity_pool_name}-codebuild")
        for prior in getattr(profile, "codebuild_prior_regions", []) or []:
            if prior == current_cb_region:
                continue  # already handled by the main loop (if enabled)
            console.print(f"Destroying orphaned CodeBuild stack in [cyan]{prior}[/cyan]: {cb_stack_name}")
            if self._delete_stack(cb_stack_name, prior, console) == 0:
                console.print(f"[green]✓ CodeBuild stack in {prior} destroyed[/green]\n")
            else:
                failed = self._get_failed_resources(cb_stack_name, prior)
                if failed:
                    all_failed_resources.extend(failed)
                    stacks_with_failures.append(f"{cb_stack_name} ({prior})")
                console.print(f"[yellow]⚠ CodeBuild stack in {prior} needs manual cleanup[/yellow]\n")

        # Clean up cached credentials for this profile
        if clear_cached_credentials(profile_name):
            console.print(f"[green]✓ Cleared cached credentials for profile '{profile_name}'[/green]")

        # Show cleanup summary at the end
        self._show_cleanup_summary(all_failed_resources, all_retained_resources, stacks_with_failures, profile, console)

        # Exit non-zero when any stack didn't delete cleanly so scripts/CI can
        # fail fast on a broken teardown. Matches deploy/package, which already
        # return 1 on failure. The summary above still surfaces what to clean up.
        return 1 if stacks_with_failures else 0

    # Per-stack deletion budgets, matched on the stack-name suffix. The monitoring
    # stack has to drain an ECS service, delete an ALB and wait for ENI detachment,
    # which routinely runs past 10 minutes; networking then waits on the NAT gateway.
    STACK_DELETE_TIMEOUT_DEFAULT = 600
    STACK_DELETE_TIMEOUTS = {
        "monitoring": 1500,
        "networking": 1200,
        "analytics": 900,
        "distribution": 900,
    }

    @classmethod
    def _delete_timeout(cls, stack_name: str) -> int:
        """Deletion budget for a stack, chosen by its name suffix."""
        for suffix, seconds in cls.STACK_DELETE_TIMEOUTS.items():
            if stack_name.endswith(f"-{suffix}"):
                return seconds
        return cls.STACK_DELETE_TIMEOUT_DEFAULT

    def _delete_stack(self, stack_name: str, region: str, console: Console) -> int:
        """Delete a CloudFormation stack using boto3.

        Returns:
            0: Success (stack deleted or doesn't exist)
            1: Partial success (DELETE_FAILED - some resources need manual cleanup)
            2: Actual error (permissions, network, etc.)
            3: Timed out while still DELETE_IN_PROGRESS (not a failure)
        """
        cf_manager = CloudFormationManager(region=region)

        # Check if stack exists
        status = cf_manager.get_stack_status(stack_name)
        if not status:
            console.print(f"[yellow]Stack {stack_name} not found or already deleted[/yellow]")
            return 0

        # If already in DELETE_FAILED, pre-clean and retry
        if status == "DELETE_FAILED":
            console.print(f"[yellow]Stack {stack_name} is in DELETE_FAILED state, retrying after cleanup...[/yellow]")

        # Pre-clean resources that block deletion (non-empty S3 buckets, Athena workgroups)
        cf_manager.pre_cleanup_stack(
            stack_name,
            on_event=lambda msg: console.print(f"  [dim]{msg}[/dim]"),
        )

        # Use progress indicator
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            task = progress.add_task(f"Deleting stack {stack_name}...", total=None)

            # Delete the stack with event tracking.
            #
            # The timeout must accommodate the slowest stack. The monitoring stack
            # drains an ECS service, deletes an ALB and waits on ENI cleanup, which
            # regularly exceeds 10 minutes; a 5-minute budget reported a spurious
            # failure while CloudFormation was still working normally.
            result = cf_manager.delete_stack(
                stack_name=stack_name,
                force=True,
                on_event=lambda e: progress.update(
                    task, description=f"Deleting {e.get('LogicalResourceId', stack_name)}..."
                ),
                timeout=self._delete_timeout(stack_name),
            )

            progress.update(task, completed=True)

            if result.success:
                return 0

            # Re-read the status before calling anything a failure. Deletion is
            # asynchronous: CloudFormation keeps going after we stop waiting.
            new_status = cf_manager.get_stack_status(stack_name)

            if new_status is None:
                # The stack is gone — it finished while we were waiting. Not a failure.
                return 0

            if new_status == "DELETE_FAILED":
                return 1  # Not an error, just needs manual cleanup

            if new_status == "DELETE_IN_PROGRESS":
                # We timed out, CloudFormation has not. Reporting "manual cleanup" here
                # invites the user to hand-delete resources mid-flight, which is how a
                # healthy delete becomes a genuinely stuck one.
                console.print(
                    f"[yellow]Still deleting after "
                    f"{self._delete_timeout(stack_name)}s — "
                    f"CloudFormation is continuing in the background.[/yellow]"
                )
                console.print("[dim]  Do not delete its resources by hand; re-run 'ccwb destroy' to confirm.[/dim]")
                return 3

            # Actual error. result.error is None on timeout, so never print a bare None.
            console.print(
                f"[red]Error deleting stack ({new_status}): {result.error or 'no error detail returned'}[/red]"
            )
            return 2

    def _get_failed_resources(self, stack_name: str, region: str) -> list[dict]:
        """Get list of resources that failed to delete from a stack."""
        cf_manager = CloudFormationManager(region=region)
        return cf_manager.get_failed_resources(stack_name)

    def _get_retained_resources(self, stack_name: str, region: str) -> list[dict]:
        """Get resources silently retained (DeletionPolicy: Retain) during deletion."""
        cf_manager = CloudFormationManager(region=region)
        return cf_manager.get_retained_resources(stack_name)

    def _show_cleanup_summary(
        self,
        failed_resources: list[dict],
        retained_resources: list[dict],
        stacks: list[str],
        profile,
        console: Console,
    ) -> None:
        """Show cleanup instructions for failed and retained resources."""
        if not failed_resources and not retained_resources and not stacks:
            console.print("\n[green]✓ All stacks destroyed successfully![/green]")
            return

        if retained_resources:
            console.print("\n[bold]Retained resources (DeletionPolicy: Retain):[/bold]")
            console.print("[dim]These were kept intentionally. Delete manually if no longer needed:[/dim]\n")
            for r in retained_resources:
                console.print(f"  • {r['logical_id']} ({r['resource_type']}): {r['physical_id']}")
            console.print()

        if not failed_resources:
            # Stacks failed to delete but no DELETE_FAILED resources are enumerable
            # (real delete error, or a client-side timeout mid-delete). Don't claim
            # success. Point the user at the affected stacks to re-run / verify.
            if stacks:
                region = profile.aws_region
                console.print("\n[yellow]⚠ The following stacks did not delete cleanly:[/yellow]")
                for stack in stacks:
                    console.print(f"  • {stack}")
                    console.print(
                        f"    [cyan]aws cloudformation delete-stack --stack-name {stack} --region {region}[/cyan]"
                    )
                console.print(
                    "\n[dim]A delete may still be in progress - re-run "
                    "[cyan]ccwb destroy[/cyan] or check the CloudFormation console to confirm.[/dim]"
                )
            return

        console.print("\n[yellow]⚠ Manual cleanup required for the following resources:[/yellow]\n")

        # Group by resource type for organized output
        by_type: dict[str, list[dict]] = {}
        for r in failed_resources:
            rtype = r["resource_type"]
            if rtype not in by_type:
                by_type[rtype] = []
            by_type[rtype].append(r)

        region = profile.aws_region

        # S3 Buckets
        if "AWS::S3::Bucket" in by_type:
            console.print("[bold]S3 Buckets (must be emptied first):[/bold]")
            for r in by_type["AWS::S3::Bucket"]:
                bucket = r["physical_id"]
                console.print(f"  • {bucket}")
                console.print(f"    [cyan]aws s3 rm s3://{bucket} --recursive[/cyan]")
                console.print(f"    [cyan]aws s3 rb s3://{bucket}[/cyan]")
            console.print()

        # CloudWatch Log Groups
        if "AWS::Logs::LogGroup" in by_type:
            console.print("[bold]CloudWatch Log Groups:[/bold]")
            for r in by_type["AWS::Logs::LogGroup"]:
                log_group = r["physical_id"]
                console.print(f"  • {log_group}")
                console.print(
                    f"    [cyan]aws logs delete-log-group --log-group-name {log_group} --region {region}[/cyan]"
                )
            console.print()

        # DynamoDB Tables
        if "AWS::DynamoDB::Table" in by_type:
            console.print("[bold]DynamoDB Tables:[/bold]")
            for r in by_type["AWS::DynamoDB::Table"]:
                table = r["physical_id"]
                console.print(f"  • {table}")
                console.print(f"    [cyan]aws dynamodb delete-table --table-name {table} --region {region}[/cyan]")
            console.print()

        # ECR Repositories
        if "AWS::ECR::Repository" in by_type:
            console.print("[bold]ECR Repositories (must delete images first):[/bold]")
            for r in by_type["AWS::ECR::Repository"]:
                repo = r["physical_id"]
                console.print(f"  • {repo}")
                console.print(
                    f"    [cyan]aws ecr delete-repository --repository-name {repo} --force --region {region}[/cyan]"
                )
            console.print()

        # Other resources
        known_types = ["AWS::S3::Bucket", "AWS::Logs::LogGroup", "AWS::DynamoDB::Table", "AWS::ECR::Repository"]
        other_types = [t for t in by_type if t not in known_types]
        if other_types:
            console.print("[bold]Other Resources:[/bold]")
            for rtype in other_types:
                for r in by_type[rtype]:
                    console.print(f"  • {r['logical_id']} ({rtype}): {r['physical_id']}")
                    console.print(f"    Reason: {r['status_reason']}")
            console.print()

        # Final instructions
        if stacks:
            console.print("[yellow]After manual cleanup, delete the failed stacks:[/yellow]")
            for stack in stacks:
                console.print(f"  [cyan]aws cloudformation delete-stack --stack-name {stack} --region {region}[/cyan]")
            console.print()

        console.print("For more information, see: assets/docs/TROUBLESHOOTING.md")
