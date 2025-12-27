#!/usr/bin/env python3
"""CLI for the Activity Report Generator."""

import logging
import os
from datetime import datetime
from typing import Optional

import click
import jinja2
import requests
import yaml

from clients import AtlassianOAuth, JiraClient, GitHubClient
from constants import DEFAULT_STORY_POINTS_FIELD
from generators import generate_report, export_to_csv, generate_all_monthly_summaries
from logging_config import setup_logging
from models import PersonReport

logger = logging.getLogger("activity_report")


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
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose logging output",
)
@click.option(
    "--story-points-field",
    default=None,
    help=f"Jira custom field ID for story points (default: {DEFAULT_STORY_POINTS_FIELD})",
)
def main(
    config: Optional[str],
    start: Optional[str],
    end: Optional[str],
    output: str,
    github_org: Optional[str],
    auth: bool,
    ai_summary: bool,
    verbose: bool,
    story_points_field: Optional[str],
):
    """Generate activity reports for team members across Jira and GitHub."""
    # Set up logging
    setup_logging(verbose=verbose)

    # Get OAuth client
    oauth = get_oauth_client()

    # Handle authorization flow
    if auth:
        logger.info("Starting Atlassian OAuth authorization flow...")
        if oauth.authorize():
            logger.info("Authorization complete! You can now run reports.")
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
    logger.info("Loading configuration from %s...", config)
    cfg = load_config(config)

    # Validate GitHub token
    github_token = validate_github_token()

    # Determine story points field: CLI > config > default
    sp_field = (
        story_points_field
        or cfg.get("story_points_field")
        or DEFAULT_STORY_POINTS_FIELD
    )

    # Initialize clients
    logger.info("Initializing API clients...")
    logger.info("Using Atlassian site: %s", oauth.site_url)
    jira_client = JiraClient(oauth, story_points_field=sp_field)
    github_client = GitHubClient(github_token)

    # Process each team member
    reports_generated = []
    csvs_generated = []

    for member in cfg["team"]:
        name = member["name"]
        jira_user = member.get("jira_username", "")
        github_user = member.get("github_username", "")

        logger.info("Processing %s...", name)

        report = PersonReport(
            name=name,
            date_range=(start, end),
        )

        # Fetch Jira data
        if jira_user:
            try:
                logger.info("Fetching Jira data for %s...", jira_user)
                report.jira.issues_assigned = jira_client.get_issues_assigned(
                    jira_user, start, end
                )
                report.jira.issues_resolved = jira_client.get_issues_resolved(
                    jira_user, start, end
                )
                report.jira.comments_made = jira_client.get_comments_made(
                    jira_user, start, end
                )
            except requests.exceptions.RequestException as e:
                logger.warning("Error fetching Jira data: %s", e)

        # Fetch GitHub data
        if github_user:
            try:
                logger.info("Fetching GitHub data for %s...", github_user)
                prs_opened, prs_merged, reviews = github_client.get_all_activity(
                    github_user, start, end, github_org
                )
                report.github.prs_opened = prs_opened
                report.github.prs_merged = prs_merged
                report.github.reviews = reviews
            except requests.exceptions.RequestException as e:
                logger.warning("Error fetching GitHub data: %s", e)

        # Generate AI summaries if requested
        if ai_summary:
            try:
                logger.info("Generating AI summaries...")
                report.monthly_summaries = generate_all_monthly_summaries(report)
            except (requests.exceptions.RequestException, KeyError, ValueError) as e:
                logger.warning("Error generating AI summaries: %s", e)

        # Export to CSV
        try:
            csv_files = export_to_csv(report, output)
            csvs_generated.extend(csv_files)
            for csv_file in csv_files:
                logger.info("Exported: %s", csv_file)
        except OSError as e:
            logger.error("Error exporting CSV: %s", e)

        # Generate report
        try:
            filepath = generate_report(report, output)
            reports_generated.append(filepath)
            logger.info("Generated: %s", filepath)
        except (OSError, jinja2.TemplateError) as e:
            logger.error("Error generating report: %s", e)

    # Summary
    logger.info("=" * 50)
    logger.info("Generated %d CSV file(s):", len(csvs_generated))
    for path in csvs_generated:
        logger.info("  - %s", path)
    logger.info("Generated %d HTML report(s):", len(reports_generated))
    for path in reports_generated:
        logger.info("  - %s", path)


if __name__ == "__main__":
    main()
