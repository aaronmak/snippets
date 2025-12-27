#!/usr/bin/env python3
"""CLI for the Activity Report Generator."""

import os
import sys
from datetime import datetime
from typing import Optional

import click
import yaml

from clients import AtlassianOAuth, JiraClient, GitHubClient
from generators import generate_report, export_to_csv, generate_all_monthly_summaries
from models import PersonReport


def get_oauth_client() -> AtlassianOAuth:
    """Get OAuth client with credentials from environment."""
    client_id = os.environ.get("ATLASSIAN_CLIENT_ID")
    client_secret = os.environ.get("ATLASSIAN_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise click.ClickException(
            "Missing required environment variables for Atlassian OAuth:\n"
            "  ATLASSIAN_CLIENT_ID - OAuth 2.0 client ID\n"
            "  ATLASSIAN_CLIENT_SECRET - OAuth 2.0 client secret\n\n"
            "Create an OAuth 2.0 app at:\n"
            "  https://developer.atlassian.com/console/myapps/\n\n"
            "Set callback URL to: http://localhost:8089/callback"
        )

    return AtlassianOAuth(client_id, client_secret)


def validate_github_token() -> str:
    """Validate GitHub token from environment."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise click.ClickException(
            "Missing GITHUB_TOKEN environment variable.\n"
            "Create a token at: https://github.com/settings/tokens\n"
            "Required scopes: repo, read:org"
        )
    return token


def load_config(config_path: str) -> dict:
    """Load and validate config file."""
    if not os.path.exists(config_path):
        raise click.ClickException(f"Config file not found: {config_path}")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    if "team" not in config or not config["team"]:
        raise click.ClickException(
            "Config must contain 'team' with at least one member"
        )

    for i, member in enumerate(config["team"]):
        required = ["name"]
        for field in required:
            if field not in member:
                raise click.ClickException(
                    f"Team member {i + 1} missing required field: {field}"
                )

    return config


@click.command()
@click.option("--config", "-c", help="Path to config YAML file")
@click.option("--start", "-s", help="Start date (YYYY-MM-DD)")
@click.option("--end", "-e", help="End date (YYYY-MM-DD)")
@click.option("--output", "-o", default="./reports", help="Output directory")
@click.option("--github-org", help="GitHub org to filter (optional)")
@click.option(
    "--auth",
    is_flag=True,
    help="Run OAuth authorization flow for Atlassian",
)
@click.option(
    "--ai-summary",
    is_flag=True,
    help="Generate AI-powered monthly summaries using Claude (requires ANTHROPIC_API_KEY)",
)
def main(
    config: Optional[str],
    start: Optional[str],
    end: Optional[str],
    output: str,
    github_org: Optional[str],
    auth: bool,
    ai_summary: bool,
):
    """Generate activity reports for team members across Jira and GitHub."""

    # Get OAuth client
    oauth = get_oauth_client()

    # Handle authorization flow
    if auth:
        print("Starting Atlassian OAuth authorization flow...", file=sys.stderr)
        if oauth.authorize():
            print("\nAuthorization complete! You can now run reports.", file=sys.stderr)
        else:
            raise click.ClickException("Authorization failed.")
        return

    # Check if we have valid tokens
    if not oauth.is_authorized():
        raise click.ClickException(
            "Not authorized with Atlassian. Run with --auth first to authorize."
        )

    # Validate required options for report generation
    if not config:
        raise click.ClickException("--config is required for generating reports")
    if not start:
        raise click.ClickException("--start is required for generating reports")
    if not end:
        raise click.ClickException("--end is required for generating reports")

    # Validate dates
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        raise click.ClickException("Dates must be in YYYY-MM-DD format")

    if start_date > end_date:
        raise click.ClickException("Start date must be before or equal to end date")

    # Load configuration
    print(f"Loading configuration from {config}...", file=sys.stderr)
    cfg = load_config(config)

    # Validate GitHub token
    github_token = validate_github_token()

    # Initialize clients
    print("Initializing API clients...", file=sys.stderr)
    print(f"Using Atlassian site: {oauth.site_url}", file=sys.stderr)
    jira_client = JiraClient(oauth)
    github_client = GitHubClient(github_token)

    # Process each team member
    reports_generated = []
    csvs_generated = []

    for member in cfg["team"]:
        name = member["name"]
        jira_user = member.get("jira_username", "")
        github_user = member.get("github_username", "")

        print(f"\nProcessing {name}...", file=sys.stderr)

        report = PersonReport(
            name=name,
            date_range=(start, end),
        )

        # Fetch Jira data
        if jira_user:
            try:
                print(f"  Fetching Jira data for {jira_user}...", file=sys.stderr)
                report.jira.issues_assigned = jira_client.get_issues_assigned(
                    jira_user, start, end
                )
                report.jira.issues_resolved = jira_client.get_issues_resolved(
                    jira_user, start, end
                )
                report.jira.comments_made = jira_client.get_comments_made(
                    jira_user, start, end
                )
            except Exception as e:
                print(f"  Warning: Error fetching Jira data: {e}", file=sys.stderr)

        # Fetch GitHub data
        if github_user:
            try:
                print(f"  Fetching GitHub data for {github_user}...", file=sys.stderr)
                prs_opened, prs_merged, reviews = github_client.get_all_activity(
                    github_user, start, end, github_org
                )
                report.github.prs_opened = prs_opened
                report.github.prs_merged = prs_merged
                report.github.reviews = reviews
            except Exception as e:
                print(f"  Warning: Error fetching GitHub data: {e}", file=sys.stderr)

        # Generate AI summaries if requested
        if ai_summary:
            try:
                print("  Generating AI summaries...", file=sys.stderr)
                report.monthly_summaries = generate_all_monthly_summaries(report)
            except Exception as e:
                print(f"  Warning: Error generating AI summaries: {e}", file=sys.stderr)

        # Export to CSV
        try:
            csv_files = export_to_csv(report, output)
            csvs_generated.extend(csv_files)
            for csv_file in csv_files:
                print(f"  Exported: {csv_file}", file=sys.stderr)
        except Exception as e:
            print(f"  Error exporting CSV: {e}", file=sys.stderr)

        # Generate report
        try:
            filepath = generate_report(report, output)
            reports_generated.append(filepath)
            print(f"  Generated: {filepath}", file=sys.stderr)
        except Exception as e:
            print(f"  Error generating report: {e}", file=sys.stderr)

    # Summary
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"Generated {len(csvs_generated)} CSV file(s):", file=sys.stderr)
    for path in csvs_generated:
        print(f"  - {path}", file=sys.stderr)
    print(f"\nGenerated {len(reports_generated)} HTML report(s):", file=sys.stderr)
    for path in reports_generated:
        print(f"  - {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
