"""Jira API client."""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests

from clients.oauth import AtlassianOAuth
from constants import (
    JiraStatus,
    JiraIssueType,
    DEFAULT_STORY_POINTS_FIELD,
    JIRA_SEARCH_ENDPOINT,
    JIRA_USER_SEARCH_ENDPOINT,
    JIRA_CHANGELOG_ENDPOINT,
    HTTP_UNAUTHORIZED,
    HTTP_TOO_MANY_REQUESTS,
    MAX_RETRIES,
    CHANGELOG_MAX_WORKERS,
    JIRA_ACCOUNT_ID_CACHE_FILE,
)

logger = logging.getLogger("activity_report")


class AtlassianClient:
    """Client for Jira REST API with OAuth 2.0 support."""

    def __init__(
        self,
        oauth: Optional[AtlassianOAuth] = None,
        cloud_id: Optional[str] = None,
        site_url: Optional[str] = None,
    ):
        """Initialize with OAuth credentials."""
        self.oauth = oauth
        self.cloud_id = cloud_id or (oauth.cloud_id if oauth else None)
        self.site_url = site_url or (oauth.site_url if oauth else None)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _get_api_url(self, endpoint: str) -> str:
        """Construct the API URL for OAuth (uses api.atlassian.com with cloud_id)."""
        endpoint = endpoint.lstrip("/")

        # For OAuth, use the Atlassian API gateway
        if endpoint.startswith("rest/api/"):
            # Jira endpoint
            return f"https://api.atlassian.com/ex/jira/{self.cloud_id}/{endpoint}"
        else:
            # Default to site URL
            return urljoin(self.site_url + "/", endpoint)

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make a request with retry logic and automatic token refresh."""
        url = self._get_api_url(endpoint)

        for attempt in range(MAX_RETRIES):
            try:
                # Get fresh access token
                access_token = self.oauth.get_access_token()
                if not access_token:
                    raise requests.exceptions.HTTPError(
                        "No valid access token. Please run with --auth to authorize."
                    )

                self.session.headers["Authorization"] = f"Bearer {access_token}"
                resp = self.session.request(method, url, **kwargs)

                if resp.status_code == HTTP_UNAUTHORIZED:
                    # Try to refresh token
                    if self.oauth.refresh_token():
                        continue
                    raise requests.exceptions.HTTPError(
                        "Jira authentication failed (401 Unauthorized).\n"
                        "Please run with --auth to re-authorize."
                    )
                if resp.status_code == HTTP_TOO_MANY_REQUESTS:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning("Rate limited, waiting %ds...", retry_after)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return resp.json() if resp.content else {}
            except requests.exceptions.RequestException as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                logger.warning("Request failed, retrying (%d/%d)...", attempt + 1, MAX_RETRIES)
                time.sleep(2**attempt)
        return {}

    def get(self, endpoint: str, **kwargs) -> dict:
        return self._request("GET", endpoint, **kwargs)


class JiraClient(AtlassianClient):
    """Jira-specific API client."""

    def __init__(self, oauth: AtlassianOAuth, story_points_field: str = DEFAULT_STORY_POINTS_FIELD):
        super().__init__(oauth=oauth)
        self._account_id_cache: dict[str, str] = {}
        self._cache_file = Path(os.path.expanduser(JIRA_ACCOUNT_ID_CACHE_FILE))
        self._load_account_id_cache()
        self.story_points_field = story_points_field

    def _load_account_id_cache(self) -> None:
        """Load account ID cache from disk."""
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r") as f:
                    self._account_id_cache = json.load(f)
                logger.debug("Loaded %d cached account IDs", len(self._account_id_cache))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not load account ID cache: %s", e)
                self._account_id_cache = {}

    def _save_account_id_cache(self) -> None:
        """Save account ID cache to disk."""
        try:
            with open(self._cache_file, "w") as f:
                json.dump(self._account_id_cache, f, indent=2)
        except OSError as e:
            logger.warning("Could not save account ID cache: %s", e)

    def get_account_id(self, username: str) -> Optional[str]:
        """Look up account ID from username/email."""
        if username in self._account_id_cache:
            return self._account_id_cache[username]

        # Try searching for the user
        try:
            resp = self.get(JIRA_USER_SEARCH_ENDPOINT, params={"query": username})
            if resp and len(resp) > 0:
                account_id = resp[0].get("accountId")
                self._account_id_cache[username] = account_id
                self._save_account_id_cache()
                return account_id
        except requests.exceptions.RequestException as e:
            logger.warning("Could not look up account ID for %s: %s", username, e)

        # Fall back to using the username as-is (might be an account ID already)
        return username

    def search_issues(
        self, jql: str, fields: Optional[list[str]] = None, expand_changelog: bool = False,
        story_points_field: str = DEFAULT_STORY_POINTS_FIELD
    ) -> list[dict]:
        """Search issues using JQL via the /rest/api/3/search/jql endpoint."""
        if fields is None:
            fields = [
                "summary",
                "description",
                "status",
                "issuetype",
                "created",
                "resolved",
                "assignee",
                "project",
                story_points_field,
            ]

        all_issues = []
        next_page_token = None
        max_results = 100

        while True:
            params = {
                "jql": jql,
                "maxResults": max_results,
                "fields": fields,
            }
            if next_page_token:
                params["nextPageToken"] = next_page_token

            resp = self._request(
                "POST",
                JIRA_SEARCH_ENDPOINT,
                json=params,
            )
            issues = resp.get("issues", [])
            all_issues.extend(issues)

            next_page_token = resp.get("nextPageToken")
            if not next_page_token:
                break

        # Fetch changelog for each issue if requested (in parallel)
        if expand_changelog and all_issues:
            self._fetch_changelogs_parallel(all_issues)

        return all_issues

    def _fetch_changelogs_parallel(self, issues: list[dict]) -> None:
        """Fetch changelogs for all issues in parallel."""
        issue_keys = [issue.get("key") for issue in issues if issue.get("key")]
        if not issue_keys:
            return

        # Create a mapping of issue_key -> issue for fast lookup
        issue_map = {issue.get("key"): issue for issue in issues if issue.get("key")}

        max_workers = min(CHANGELOG_MAX_WORKERS, len(issue_keys))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all changelog fetch tasks
            future_to_key = {
                executor.submit(self._get_issue_changelog, key): key
                for key in issue_keys
            }

            # Collect results as they complete
            for future in as_completed(future_to_key):
                issue_key = future_to_key[future]
                try:
                    changelog = future.result()
                    issue_map[issue_key]["changelog"] = changelog
                except Exception as e:
                    logger.warning("Failed to fetch changelog for %s: %s", issue_key, e)
                    issue_map[issue_key]["changelog"] = {"histories": []}

    def _get_issue_changelog(self, issue_key: str) -> dict:
        """Fetch changelog for a specific issue."""
        try:
            resp = self._request(
                "GET",
                JIRA_CHANGELOG_ENDPOINT.format(issue_key=issue_key),
            )
            return {"histories": resp.get("values", [])}
        except requests.exceptions.RequestException:
            return {"histories": []}

    def get_issues_assigned(
        self, username: str, start_date: str, end_date: str
    ) -> list[dict]:
        """Get issues created and assigned to user in date range."""
        account_id = self.get_account_id(username)
        jql = f'assignee = "{account_id}" AND created >= "{start_date}" AND created <= "{end_date}" AND status NOT IN ("{JiraStatus.CANCELLED}", "{JiraStatus.DISMISSED}") AND issuetype != {JiraIssueType.EPIC}'
        issues = self.search_issues(jql, expand_changelog=True, story_points_field=self.story_points_field)
        return [self._format_issue(i, self.story_points_field) for i in issues]

    def _get_two_years_ago(self) -> str:
        """Get date string for 2 years ago."""
        two_years_ago = datetime.now().replace(year=datetime.now().year - 2)
        return two_years_ago.strftime("%Y-%m-%d")

    def get_issues_resolved(
        self, username: str, start_date: str, end_date: str
    ) -> list[dict]:
        """Get issues resolved/closed by user in date range."""
        account_id = self.get_account_id(username)
        # Fetch issues that were resolved in the date range
        # Add created date bound to satisfy JIRA's unbounded query restriction
        two_years_ago = self._get_two_years_ago()
        jql = f'assignee = "{account_id}" AND resolved >= "{start_date}" AND resolved <= "{end_date}" AND created >= "{two_years_ago}" AND issuetype != {JiraIssueType.EPIC}'
        issues = self.search_issues(jql, expand_changelog=True, story_points_field=self.story_points_field)
        return [self._format_issue(i, self.story_points_field) for i in issues]

    def get_comments_made(
        self, username: str, start_date: str, end_date: str
    ) -> list[dict]:
        """Get comments made by user. Note: This is limited by Jira's API capabilities."""
        # Jira doesn't have a direct way to search comments by author and date
        # We'll search for issues the user commented on and filter
        account_id = self.get_account_id(username)

        # Search for issues where this user is in the comments
        # This is an approximation - Jira's comment search is limited
        try:
            # Use JQL to find issues the user participated in
            jql = f'issueFunction in commented("by {account_id}") AND updated >= "{start_date}" AND updated <= "{end_date}" AND issuetype != {JiraIssueType.EPIC}'
            issues = self.search_issues(jql, fields=["key", "summary"])
            return [
                {"issue_key": i["key"], "issue_summary": i["fields"].get("summary", "")}
                for i in issues
            ]
        except requests.exceptions.RequestException:
            # issueFunction might not be available, return empty
            return []

    def _extract_text_from_adf(self, adf: Optional[dict]) -> str:
        """Extract plain text from Atlassian Document Format (ADF)."""
        if not adf:
            return ""
        if isinstance(adf, str):
            return adf

        texts: list[str] = []

        def extract(node: dict | list) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text":
                    texts.append(node.get("text", ""))
                for child in node.get("content", []):
                    extract(child)
            elif isinstance(node, list):
                for item in node:
                    extract(item)

        extract(adf)
        return " ".join(texts)

    def _get_status_change_date(
        self, issue: dict, target_statuses: list[JiraStatus | str]
    ) -> Optional[str]:
        """Extract the date when issue status changed to one of the target statuses.

        Searches the changelog for the most recent transition to Closed, Resolved, or Done.
        Returns the date in YYYY-MM-DD format, or None if not found.
        """
        changelog = issue.get("changelog", {})
        histories = changelog.get("histories", [])

        # Search from most recent to oldest
        for history in reversed(histories):
            for item in history.get("items", []):
                if item.get("field") == "status":
                    to_status = item.get("toString", "")
                    if to_status in [s.value if isinstance(s, JiraStatus) else s for s in target_statuses]:
                        # Return the date of this change
                        created = history.get("created", "")
                        return created[:10] if created else None

        return None

    def get_all_activity(
        self, username: str, start_date: str, end_date: str
    ) -> tuple[list[dict], list[dict]]:
        """Fetch all Jira activity (issues assigned and resolved).

        Returns:
            Tuple of (issues_assigned, issues_resolved)
        """
        issues_assigned = self.get_issues_assigned(username, start_date, end_date)
        issues_resolved = self.get_issues_resolved(username, start_date, end_date)

        if not issues_assigned and not issues_resolved:
            logger.warning(
                "No Jira data returned for user '%s' between %s and %s",
                username,
                start_date,
                end_date,
            )

        return issues_assigned, issues_resolved

    def _format_issue(self, issue: dict, story_points_field: str = DEFAULT_STORY_POINTS_FIELD) -> dict:
        """Format issue for display."""
        fields = issue.get("fields", {})
        story_points = fields.get(story_points_field)
        description = self._extract_text_from_adf(fields.get("description"))

        # Get resolved date: prefer the resolved field, fall back to changelog
        resolved = fields.get("resolved", "")[:10] if fields.get("resolved") else ""
        if not resolved:
            # Try to get the date from changelog when status changed to Closed/Resolved/Done
            resolved = (
                self._get_status_change_date(issue, [JiraStatus.CLOSED, JiraStatus.RESOLVED, JiraStatus.DONE])
                or ""
            )

        return {
            "key": issue.get("key"),
            "summary": fields.get("summary", ""),
            "description": description,
            "status": fields.get("status", {}).get("name", ""),
            "type": fields.get("issuetype", {}).get("name", ""),
            "created": fields.get("created", "")[:10] if fields.get("created") else "",
            "resolved": resolved,
            "story_points": int(story_points) if story_points is not None else None,
            "url": f"{self.site_url}/browse/{issue.get('key')}",
        }
