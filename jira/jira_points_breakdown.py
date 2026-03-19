#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jira",
#     "python-dotenv",
# ]
# ///
"""
JIRA Story Points Breakdown
============================
Fetches tickets from the last N days, groups by parent summary,
and prints % of story points per parent.

Environment variables (set in .env or export):
    JIRA_BASE_URL      - e.g. https://yourcompany.atlassian.net
    JIRA_EMAIL         - your Atlassian email
    JIRA_API_TOKEN     - API token from https://id.atlassian.com/manage-profile/security/api-tokens
    JIRA_PROJECT_KEY   - project key (default: PDO)
    LOOKBACK_DAYS      - number of days to look back (default: 14)
    STORY_POINTS_FIELD - custom field ID for story points (default: customfield_10053)
"""

import os
from collections import defaultdict

from dotenv import load_dotenv
from jira import JIRA

load_dotenv(override=True)

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "PDO")
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "14"))
STORY_POINTS_FIELD = os.getenv("STORY_POINTS_FIELD", "customfield_10053")


def main():
    print(f"JIRA Base URL: {JIRA_BASE_URL}")
    print(f"JIRA Email: {JIRA_EMAIL}\n\n")
    client = JIRA(server=JIRA_BASE_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))

    print(f"📊 JIRA Story Points Breakdown — Project: {PROJECT_KEY}")
    print(f"   Period: last {LOOKBACK_DAYS} days")
    print("=" * 60)

    jql = (
        f"project = {PROJECT_KEY} "
        f"AND issuetype not in (Epic, Sub-task) "
        f"AND resolutiondate >= -{LOOKBACK_DAYS}d "
        f'AND status != "Dismissed"'
    )

    print("\n🔍 Fetching issues...")
    issues = client.search_issues(
        jql,
        fields=f"summary,{STORY_POINTS_FIELD},parent",
        maxResults=False,
    )
    print(f"   Found {len(issues)} issues")

    # Group by parent summary and sum story points
    parent_points: dict[str, float] = defaultdict(float)
    total_points = 0.0

    for issue in issues:
        sp = getattr(issue.fields, STORY_POINTS_FIELD, None)
        if not sp:
            continue
        sp = float(sp)

        parent = getattr(issue.fields, "parent", None)
        parent_summary = parent.fields.summary if parent else "(No Parent)"

        parent_points[parent_summary] += sp
        total_points += sp

    if total_points == 0:
        print("⚠️  No story points found in the given period.")
        return

    # Sort by parent summary alphabetically
    sorted_parents = sorted(parent_points.items(), key=lambda x: x[0])

    print(f"\n{'PARENT':<50} {'PTS':>6} {'%':>7}")
    print("-" * 65)
    for parent_summary, pts in sorted_parents:
        pct = (pts / total_points) * 100
        label = parent_summary[:48]
        print(f"  {label:<48} {pts:>6.1f} {pct:>6.1f}%")
    print("-" * 65)
    print(f"  {'TOTAL':<48} {total_points:>6.1f} {'100.0%':>7}")


if __name__ == "__main__":
    main()
