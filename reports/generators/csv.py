"""CSV export functionality for activity reports."""

import csv
import os

from models import PersonReport


def export_to_csv(report: PersonReport, output_dir: str) -> list[str]:
    """Export report data to CSV files."""
    os.makedirs(output_dir, exist_ok=True)
    safe_name = report.name.lower().replace(" ", "_")
    start, end = report.date_range
    csv_files = []

    # Jira issues assigned CSV
    if report.jira.issues_assigned:
        jira_filename = f"{safe_name}_jira_issues_assigned_{start}_{end}.csv"
        jira_filepath = os.path.join(output_dir, jira_filename)
        with open(jira_filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "key",
                    "summary",
                    "description",
                    "type",
                    "status",
                    "story_points",
                    "created",
                    "resolved",
                    "url",
                ],
            )
            writer.writeheader()
            writer.writerows(report.jira.issues_assigned)
        csv_files.append(jira_filepath)

    # Jira issues resolved CSV
    if report.jira.issues_resolved:
        jira_resolved_filename = f"{safe_name}_jira_issues_resolved_{start}_{end}.csv"
        jira_resolved_filepath = os.path.join(output_dir, jira_resolved_filename)
        with open(jira_resolved_filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "key",
                    "summary",
                    "description",
                    "type",
                    "status",
                    "story_points",
                    "created",
                    "resolved",
                    "url",
                ],
            )
            writer.writeheader()
            writer.writerows(report.jira.issues_resolved)
        csv_files.append(jira_resolved_filepath)

    # GitHub activity CSV (PRs opened + merged + reviews combined)
    github_activity = []
    for pr in report.github.prs_opened:
        github_activity.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "description": pr.get("description", ""),
                "activity_type": "pr_opened",
                "repo": pr.get("repo", ""),
                "date": pr.get("created_at", ""),
                "url": pr.get("url", ""),
            }
        )
    for pr in report.github.prs_merged:
        github_activity.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "description": pr.get("description", ""),
                "activity_type": "pr_merged",
                "repo": pr.get("repo", ""),
                "date": pr.get("merged_at") or pr.get("created_at", ""),
                "url": pr.get("url", ""),
            }
        )
    for pr in report.github.reviews:
        github_activity.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "description": pr.get("description", ""),
                "activity_type": "review",
                "repo": pr.get("repo", ""),
                "date": pr.get("created_at", ""),
                "url": pr.get("url", ""),
            }
        )

    if github_activity:
        github_filename = f"{safe_name}_github_activity_{start}_{end}.csv"
        github_filepath = os.path.join(output_dir, github_filename)
        with open(github_filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "number",
                    "title",
                    "description",
                    "activity_type",
                    "repo",
                    "date",
                    "url",
                ],
            )
            writer.writeheader()
            writer.writerows(github_activity)
        csv_files.append(github_filepath)

    return csv_files
