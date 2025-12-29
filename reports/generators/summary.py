"""AI-powered summary generation using Claude."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import anthropic

from constants import SUMMARY_MAX_WORKERS
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
    except anthropic.APIError as e:
        logger.warning("Failed to generate AI summary: %s", e)
        return f"Summary generation failed: {str(e)}"


def _generate_summary_for_month(
    name: str,
    month_key: str,
    month_display: str,
    jira_issues_resolved: list[dict],
    github_prs_merged: list[dict],
    github_reviews: list[dict],
) -> MonthlySummary:
    """Generate a summary for a single month (used for parallel execution)."""
    logger.info("Generating summary for %s...", month_display)

    # Filter data for this month
    jira_resolved = filter_items_by_month(jira_issues_resolved, "resolved", month_key)
    prs_merged = filter_items_by_month(github_prs_merged, "merged_at", month_key)
    reviews = filter_items_by_month(github_reviews, "merged_at", month_key)

    summary_text = generate_monthly_summary(
        name,
        month_display,
        jira_resolved,
        prs_merged,
        reviews,
    )

    return MonthlySummary(
        month=month_key,
        month_display=month_display,
        summary=summary_text,
    )


def generate_all_monthly_summaries(
    report: PersonReport,
) -> list[MonthlySummary]:
    """Generate summaries for each month in the report date range (in parallel)."""
    start_date, end_date = report.date_range
    months = get_months_in_range(start_date, end_date)

    if not months:
        return []

    max_workers = min(SUMMARY_MAX_WORKERS, len(months))
    summaries_dict: dict[str, MonthlySummary] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all summary generation tasks
        future_to_month = {
            executor.submit(
                _generate_summary_for_month,
                report.name,
                month_key,
                month_display,
                report.jira.issues_resolved,
                report.github.prs_merged,
                report.github.reviews,
            ): month_key
            for month_key, month_display, _ in months
        }

        # Collect results as they complete
        for future in as_completed(future_to_month):
            month_key = future_to_month[future]
            try:
                summary = future.result()
                summaries_dict[month_key] = summary
            except Exception as e:
                logger.warning("Failed to generate summary for %s: %s", month_key, e)
                # Create a fallback summary
                month_display = next(
                    (m[1] for m in months if m[0] == month_key), month_key
                )
                summaries_dict[month_key] = MonthlySummary(
                    month=month_key,
                    month_display=month_display,
                    summary=f"Summary generation failed: {str(e)}",
                )

    # Return summaries in chronological order
    return [summaries_dict[month_key] for month_key, _, _ in months if month_key in summaries_dict]
