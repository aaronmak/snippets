#!/usr/bin/env python3
"""CLI for the Activity Report Generator."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import click
import jinja2
import requests
import yaml

from pydantic import ValidationError

from clients import AtlassianOAuth, JiraClient, GitHubClient
from config import Config, TeamMember
from constants import DEFAULT_STORY_POINTS_FIELD
from generators import generate_report, export_to_csv, generate_all_monthly_summaries
from logging_config import setup_logging
from models import PersonReport

logger = logging.getLogger("activity_report")

# Default number of parallel workers
DEFAULT_MAX_WORKERS = 4


def process_team_member(
    member: TeamMember,
    jira_client: JiraClient,
    github_client: GitHubClient,
    start: str,
    end: str,
    github_org: Optional[str],
    ai_summary: bool,
    output: str,
) -> tuple[list[str], list[str]]:
    """Process a single team member and return (csv_files, report_files)."""
    name = member.name
    jira_user = member.jira_username or ""
    github_user = member.github_username or ""

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
            logger.warning("Error fetching Jira data for %s: %s", name, e)

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
            logger.warning("Error fetching GitHub data for %s: %s", name, e)

    # Generate AI summaries if requested
    if ai_summary:
        try:
            logger.info("Generating AI summaries for %s...", name)
            report.monthly_summaries = generate_all_monthly_summaries(report)
        except (requests.exceptions.RequestException, KeyError, ValueError) as e:
            logger.warning("Error generating AI summaries for %s: %s", name, e)

    csv_files = []
    report_files = []

    # Export to CSV
    try:
        csv_files = export_to_csv(report, output)
        for csv_file in csv_files:
            logger.info("Exported: %s", csv_file)
    except OSError as e:
        logger.error("Error exporting CSV for %s: %s", name, e)

    # Generate report
    try:
        filepath = generate_report(report, output)
        report_files.append(filepath)
        logger.info("Generated: %s", filepath)
    except (OSError, jinja2.TemplateError) as e:
        logger.error("Error generating report for %s: %s", name, e)

    return csv_files, report_files


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


def load_config(config_path: str) -> Config:
    """Load and validate config file using Pydantic."""
    if not os.path.exists(config_path):
        raise click.ClickException(f"Config file not found: {config_path}")

    with open(config_path, "r") as f:
        raw_config = yaml.safe_load(f)

    try:
        return Config.model_validate(raw_config)
    except ValidationError as e:
        errors = []
        for error in e.errors():
            loc = " -> ".join(str(x) for x in error["loc"])
            errors.append(f"  {loc}: {error['msg']}")
        raise click.ClickException(
            f"Configuration validation failed:\n" + "\n".join(errors)
        )


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
    sp_field = story_points_field or cfg.story_points_field

    # Initialize clients
    logger.info("Initializing API clients...")
    logger.info("Using Atlassian site: %s", oauth.site_url)
    jira_client = JiraClient(oauth, story_points_field=sp_field)
    github_client = GitHubClient(github_token)

    # Process each team member in parallel
    reports_generated = []
    csvs_generated = []

    max_workers = min(DEFAULT_MAX_WORKERS, len(cfg.team))
    logger.info("Processing %d team members with %d workers...", len(cfg.team), max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_team_member,
                member,
                jira_client,
                github_client,
                start,
                end,
                github_org,
                ai_summary,
                output,
            ): member.name
            for member in cfg.team
        }

        for future in as_completed(futures):
            member_name = futures[future]
            try:
                csv_files, report_files = future.result()
                csvs_generated.extend(csv_files)
                reports_generated.extend(report_files)
            except Exception as e:
                logger.error("Error processing %s: %s", member_name, e)

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
