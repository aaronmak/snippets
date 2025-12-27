"""GitHub API client using GraphQL."""

import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import requests


class GitHubClient:
    """GitHub API client using GraphQL."""

    # GraphQL query to fetch all PR activity in a single request
    ACTIVITY_QUERY = """
    query($q1: String!, $q2: String!, $q3: String!, $first: Int!, $after1: String, $after2: String, $after3: String) {
      prsOpened: search(query: $q1, type: ISSUE, first: $first, after: $after1) {
        nodes {
          ... on PullRequest {
            number
            title
            body
            state
            url
            createdAt
            mergedAt
            repository { nameWithOwner }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
      prsMerged: search(query: $q2, type: ISSUE, first: $first, after: $after2) {
        nodes {
          ... on PullRequest {
            number
            title
            body
            state
            url
            createdAt
            mergedAt
            repository { nameWithOwner }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
      reviewed: search(query: $q3, type: ISSUE, first: $first, after: $after3) {
        nodes {
          ... on PullRequest {
            number
            title
            body
            state
            url
            createdAt
            mergedAt
            repository { nameWithOwner }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """

    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
            }
        )
        self.graphql_url = "https://api.github.com/graphql"

    def _graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        """Execute a GraphQL query."""
        for attempt in range(3):
            try:
                resp = self.session.post(
                    self.graphql_url,
                    json={
                        "query": query,
                        "variables": variables or {},
                    },
                )
                if resp.status_code == 403:  # Rate limited
                    reset_time = int(
                        resp.headers.get("X-RateLimit-Reset", time.time() + 60)
                    )
                    wait_time = max(reset_time - time.time(), 0) + 1
                    print(
                        f"  Rate limited, waiting {int(wait_time)}s...", file=sys.stderr
                    )
                    time.sleep(wait_time)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    raise
                print(
                    f"  GraphQL request failed, retrying ({attempt + 1}/3)...",
                    file=sys.stderr,
                )
                time.sleep(2**attempt)
        return {}

    def _format_pr_graphql(self, node: dict) -> dict:
        """Format PR from GraphQL response for display."""
        if not node:
            return {}
        repo = node.get("repository", {})
        created_at = node.get("createdAt", "")
        merged_at = node.get("mergedAt", "")
        return {
            "number": node.get("number"),
            "title": node.get("title", ""),
            "description": node.get("body", "") or "",
            "state": node.get("state", "").lower(),
            "repo": repo.get("nameWithOwner", ""),
            "created_at": created_at[:10] if created_at else "",
            "merged_at": merged_at[:10] if merged_at else "",
            "url": node.get("url", ""),
        }

    def _get_two_years_ago(self) -> str:
        """Get date string for 2 years ago."""
        two_years_ago = datetime.now().replace(year=datetime.now().year - 2)
        return two_years_ago.strftime("%Y-%m-%d")

    def _get_monthly_ranges(
        self, start_date: str, end_date: str
    ) -> list[tuple[str, str]]:
        """Split a date range into monthly chunks to avoid GitHub's 1000 result limit."""
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        ranges = []
        current = start

        while current <= end:
            # Get the last day of the current month
            if current.month == 12:
                next_month = current.replace(year=current.year + 1, month=1, day=1)
            else:
                next_month = current.replace(month=current.month + 1, day=1)
            month_end = next_month - timedelta(days=1)

            # Clamp to the actual end date
            chunk_end = min(month_end, end)

            ranges.append(
                (current.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d"))
            )

            # Move to the first day of the next month
            current = next_month

        return ranges

    def _fetch_activity_chunk(
        self, username: str, start_date: str, end_date: str, org: Optional[str] = None
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Fetch GitHub activity for a single date chunk."""
        two_years_ago = self._get_two_years_ago()
        org_filter = f" org:{org}" if org else ""

        # Build query strings for each search
        q1 = f"is:pr author:{username} created:{start_date}..{end_date}{org_filter}"
        q2 = f"is:pr is:merged author:{username} merged:{start_date}..{end_date} created:>{two_years_ago}{org_filter}"
        q3 = f"is:pr reviewed-by:{username} updated:{start_date}..{end_date} created:>{two_years_ago}{org_filter}"

        # Collect all results with pagination support
        prs_opened = []
        prs_merged = []
        reviews = []

        # Track cursors for each query
        cursors = {"after1": None, "after2": None, "after3": None}
        has_more = {"prsOpened": True, "prsMerged": True, "reviewed": True}

        while any(has_more.values()):
            variables = {
                "q1": q1,
                "q2": q2,
                "q3": q3,
                "first": 100,
                **cursors,
            }

            result = self._graphql(self.ACTIVITY_QUERY, variables)

            if "errors" in result:
                error_msg = result["errors"][0].get("message", "Unknown GraphQL error")
                raise requests.exceptions.HTTPError(
                    f"GitHub GraphQL error: {error_msg}"
                )

            data = result.get("data", {})

            # Process prsOpened
            if has_more["prsOpened"]:
                opened_data = data.get("prsOpened", {})
                nodes = opened_data.get("nodes", [])
                prs_opened.extend([self._format_pr_graphql(n) for n in nodes if n])
                page_info = opened_data.get("pageInfo", {})
                if page_info.get("hasNextPage"):
                    cursors["after1"] = page_info.get("endCursor")
                else:
                    has_more["prsOpened"] = False

            # Process prsMerged
            if has_more["prsMerged"]:
                merged_data = data.get("prsMerged", {})
                nodes = merged_data.get("nodes", [])
                prs_merged.extend([self._format_pr_graphql(n) for n in nodes if n])
                page_info = merged_data.get("pageInfo", {})
                if page_info.get("hasNextPage"):
                    cursors["after2"] = page_info.get("endCursor")
                else:
                    has_more["prsMerged"] = False

            # Process reviewed
            if has_more["reviewed"]:
                reviewed_data = data.get("reviewed", {})
                nodes = reviewed_data.get("nodes", [])
                reviews.extend([self._format_pr_graphql(n) for n in nodes if n])
                page_info = reviewed_data.get("pageInfo", {})
                if page_info.get("hasNextPage"):
                    cursors["after3"] = page_info.get("endCursor")
                else:
                    has_more["reviewed"] = False

            # If no more data to fetch for any query, break
            if not any(has_more.values()):
                break

        return prs_opened, prs_merged, reviews

    def get_all_activity(
        self, username: str, start_date: str, end_date: str, org: Optional[str] = None
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Fetch all GitHub activity (PRs opened, merged, reviews) chunked by month.

        Splits the date range into monthly chunks to avoid GitHub's 1000 result limit
        per search query.

        Returns:
            Tuple of (prs_opened, prs_merged, reviews)
        """
        monthly_ranges = self._get_monthly_ranges(start_date, end_date)

        all_prs_opened = []
        all_prs_merged = []
        all_reviews = []

        # Track seen URLs to deduplicate across months (reviews use 'updated' which can span months)
        seen_opened = set()
        seen_merged = set()
        seen_reviews = set()

        for chunk_start, chunk_end in monthly_ranges:
            prs_opened, prs_merged, reviews = self._fetch_activity_chunk(
                username, chunk_start, chunk_end, org
            )

            # Deduplicate PRs opened
            for pr in prs_opened:
                url = pr.get("url")
                if url and url not in seen_opened:
                    seen_opened.add(url)
                    all_prs_opened.append(pr)

            # Deduplicate PRs merged
            for pr in prs_merged:
                url = pr.get("url")
                if url and url not in seen_merged:
                    seen_merged.add(url)
                    all_prs_merged.append(pr)

            # Deduplicate reviews (most likely to have duplicates due to 'updated' filter)
            for pr in reviews:
                url = pr.get("url")
                if url and url not in seen_reviews:
                    seen_reviews.add(url)
                    all_reviews.append(pr)

        return all_prs_opened, all_prs_merged, all_reviews
