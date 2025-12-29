"""HTML report generation."""

import os
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from models import PersonReport


def get_week_labels(start_date: str, end_date: str) -> list[str]:
    """Generate list of week labels (Mondays) between start and end dates."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    # Find the Monday of the week containing start_date
    start_monday = start - timedelta(days=start.weekday())

    weeks = []
    current = start_monday
    while current <= end:
        weeks.append(current.strftime("%b %d"))
        current += timedelta(days=7)

    return weeks


def aggregate_by_week(
    items: list[dict], date_field: str, start_date: str, end_date: str
) -> list[int]:
    """Aggregate items by week, returning counts for each week in the date range."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    # Find the Monday of the week containing start_date
    start_monday = start - timedelta(days=start.weekday())

    # Build list of week start dates
    week_starts = []
    current = start_monday
    while current <= end:
        week_starts.append(current)
        current += timedelta(days=7)

    # Count items per week
    counts = [0] * len(week_starts)
    for item in items:
        date_str = item.get(date_field, "")
        if not date_str:
            continue
        try:
            item_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
            # Find which week this belongs to
            for i, week_start in enumerate(week_starts):
                week_end = week_start + timedelta(days=6)
                if week_start <= item_date <= week_end:
                    counts[i] += 1
                    break
        except ValueError:
            continue

    return counts


def aggregate_jira_by_week(
    issues: list[dict], start_date: str, end_date: str
) -> tuple[list[int], list[int]]:
    """Aggregate JIRA issues by resolved date, returning (ticket counts, story points) per week."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    # Find the Monday of the week containing start_date
    start_monday = start - timedelta(days=start.weekday())

    # Build list of week start dates
    week_starts = []
    current = start_monday
    while current <= end:
        week_starts.append(current)
        current += timedelta(days=7)

    # Count tickets and sum story points per week
    ticket_counts = [0] * len(week_starts)
    points_sums = [0] * len(week_starts)

    for issue in issues:
        resolved_str = issue.get("resolved", "")
        if not resolved_str:
            continue
        try:
            resolved_date = datetime.strptime(resolved_str[:10], "%Y-%m-%d")
            # Find which week this belongs to
            for i, week_start in enumerate(week_starts):
                week_end = week_start + timedelta(days=6)
                if week_start <= resolved_date <= week_end:
                    ticket_counts[i] += 1
                    points = issue.get("story_points")
                    if points is not None:
                        points_sums[i] += points
                    break
        except ValueError:
            continue

    return ticket_counts, points_sums


def generate_report(report: PersonReport, output_dir: str) -> str:
    """Generate HTML report for a person."""
    # Get template directory
    template_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("report.html")

    # Compute weekly activity data for charts
    start_date, end_date = report.date_range
    week_labels = get_week_labels(start_date, end_date)

    # GitHub weekly data
    prs_opened_by_week = aggregate_by_week(
        report.github.prs_opened, "created_at", start_date, end_date
    )
    prs_merged_by_week = aggregate_by_week(
        report.github.prs_merged, "merged_at", start_date, end_date
    )
    reviews_by_week = aggregate_by_week(
        report.github.reviews, "updated_at", start_date, end_date
    )

    # JIRA weekly data (tickets closed and story points)
    jira_tickets_by_week, jira_points_by_week = aggregate_jira_by_week(
        report.jira.issues_resolved, start_date, end_date
    )

    html = template.render(
        name=report.name,
        start_date=report.date_range[0],
        end_date=report.date_range[1],
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        jira=report.jira,
        github=report.github,
        week_labels=week_labels,
        prs_opened_by_week=prs_opened_by_week,
        prs_merged_by_week=prs_merged_by_week,
        reviews_by_week=reviews_by_week,
        jira_tickets_by_week=jira_tickets_by_week,
        jira_points_by_week=jira_points_by_week,
        monthly_summaries=report.monthly_summaries,
    )

    # Create filename
    safe_name = report.name.lower().replace(" ", "_")
    filename = f"{safe_name}_{report.date_range[0]}_{report.date_range[1]}.html"
    filepath = os.path.join(output_dir, filename)

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    return filepath
