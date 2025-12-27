"""AI-powered summary generation using Claude."""

import logging
from datetime import datetime, timedelta

import anthropic

from models import PersonReport, MonthlySummary

logger = logging.getLogger("activity_report")


def get_months_in_range(start_date: str, end_date: str) -> list[tuple[str, str, str]]:
    """Get list of (YYYY-MM, display_name, end_of_month) for each month in range."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    months = []
    current = start.replace(day=1)

    while current <= end:
        month_key = current.strftime("%Y-%m")
        month_display = current.strftime("%B %Y")

        # Get last day of month
        if current.month == 12:
            next_month = current.replace(year=current.year + 1, month=1)
        else:
            next_month = current.replace(month=current.month + 1)
        last_day = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")

        months.append((month_key, month_display, last_day))

        # Move to next month
        current = next_month

    return months


def filter_items_by_month(
    items: list[dict], date_field: str, month_key: str
) -> list[dict]:
    """Filter items to only those in the specified month (YYYY-MM)."""
    return [item for item in items if item.get(date_field, "").startswith(month_key)]


def generate_monthly_summary(
    name: str,
    month_display: str,
    jira_resolved: list[dict],
    prs_merged: list[dict],
    reviews: list[dict],
) -> str:
    """Generate a monthly summary using Claude."""
    # Build context for Claude
    context_parts = []

    if jira_resolved:
        resolved_summary = "JIRA Issues Resolved:\n"
        for issue in jira_resolved[:20]:
            points = (
                f", {issue['story_points']} pts" if issue.get("story_points") else ""
            )
            resolved_summary += f"- [{issue['key']}] {issue['summary']} (Type: {issue['type']}{points})\n"
        context_parts.append(resolved_summary)

    if prs_merged:
        pr_summary = "GitHub PRs Merged:\n"
        for pr in prs_merged[:20]:
            desc = (
                pr.get("description", "")[:200] + "..."
                if len(pr.get("description", "")) > 200
                else pr.get("description", "")
            )
            pr_summary += f"- [{pr['repo']}#{pr['number']}] {pr['title']}\n"
            if desc:
                pr_summary += f"  Description: {desc}\n"
        context_parts.append(pr_summary)

    if reviews:
        review_summary = f"Code Reviews: {len(reviews)} PRs reviewed\n"
        context_parts.append(review_summary)

    if not context_parts:
        return "No significant activity recorded for this month."

    context = "\n".join(context_parts)

    # Call Claude API
    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": f"""Based on the following activity data for {name} in {month_display}, write a concise professional summary (2-4 sentences) highlighting key accomplishments and focus areas. Focus on the impact and themes of the work, not just listing items. Do not use flowery language. Be succinct.

{context}

Write the summary in third person, using their name. Be specific about what was accomplished.""",
                }
            ],
        )
        return message.content[0].text
    except Exception as e:
        logger.warning("Failed to generate AI summary: %s", e)
        return f"Summary generation failed: {str(e)}"


def generate_all_monthly_summaries(
    report: PersonReport,
) -> list[MonthlySummary]:
    """Generate summaries for each month in the report date range."""
    start_date, end_date = report.date_range
    months = get_months_in_range(start_date, end_date)
    summaries = []

    for month_key, month_display, _ in months:
        logger.info("Generating summary for %s...", month_display)

        # Filter data for this month (based on completion date: resolved for JIRA, merged for PRs)
        jira_resolved = filter_items_by_month(
            report.jira.issues_resolved, "resolved", month_key
        )
        prs_merged = filter_items_by_month(
            report.github.prs_merged, "merged_at", month_key
        )
        reviews = filter_items_by_month(report.github.reviews, "created_at", month_key)

        summary_text = generate_monthly_summary(
            report.name,
            month_display,
            jira_resolved,
            prs_merged,
            reviews,
        )

        summaries.append(
            MonthlySummary(
                month=month_key,
                month_display=month_display,
                summary=summary_text,
            )
        )

    return summaries
