#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jira",
# ]
# ///
"""
JIRA Story Points Breakdown
============================
Fetches tickets resolved in a date range, groups by parent summary,
and prints % of story points per parent.
"""

import argparse
from collections import defaultdict
from datetime import date, timedelta

from jira import JIRA


def parse_args():
    parser = argparse.ArgumentParser(description="JIRA Story Points Breakdown")
    parser.add_argument("--base-url", required=True, help="e.g. https://yourcompany.atlassian.net")
    parser.add_argument("--email", required=True, help="Your Atlassian email")
    parser.add_argument(
        "--api-token",
        required=True,
        help="API token from https://id.atlassian.com/manage-profile/security/api-tokens",
    )
    parser.add_argument("--project-key", default="PDO", help="Project key (default: PDO)")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD), default: 30 days before end date")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD), default: today")
    parser.add_argument(
        "--story-points-field",
        default="customfield_10053",
        help="Custom field ID for story points (default: customfield_10053)",
    )
    args = parser.parse_args()

    end_date = date.fromisoformat(args.end_date) if args.end_date else date.today()
    start_date = date.fromisoformat(args.start_date) if args.start_date else end_date - timedelta(days=30)
    args.start_date = start_date
    args.end_date = end_date
    return args


def main():
    args = parse_args()
    base_url = args.base_url.rstrip("/")

    print(f"JIRA Base URL: {base_url}")
    print(f"JIRA Email: {args.email}\n\n")
    client = JIRA(server=base_url, basic_auth=(args.email, args.api_token))

    print(f"📊 JIRA Story Points Breakdown — Project: {args.project_key}")
    print(f"   Period: {args.start_date} to {args.end_date}")
    print("=" * 60)

    jql = (
        f"project = {args.project_key} "
        f"AND issuetype not in (Epic, Sub-task) "
        f'AND resolutiondate >= "{args.start_date}" '
        f'AND resolutiondate <= "{args.end_date}" '
        f'AND status != "Dismissed"'
    )

    print("\n🔍 Fetching issues...")
    issues = client.search_issues(
        jql,
        fields=f"summary,{args.story_points_field},parent",
        maxResults=False,
    )
    print(f"   Found {len(issues)} issues")

    # Group by parent summary and sum story points
    parent_points: dict[str, float] = defaultdict(float)
    total_points = 0.0

    for issue in issues:
        sp = getattr(issue.fields, args.story_points_field, None)
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
