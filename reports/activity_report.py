#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests>=2.31.0",
#     "pyyaml>=6.0",
#     "jinja2>=3.1.0",
#     "click>=8.1.0",
# ]
# ///
"""
Multi-Platform Activity Report Generator

Generates individual HTML reports summarizing activity across Jira, Confluence,
and GitHub for multiple team members.
"""

import csv
import os
import sys
import time
import json
import webbrowser
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlencode, parse_qs, urlparse

import click
import requests
import yaml
from jinja2 import Template


# =============================================================================
# OAuth 2.0 3LO for Atlassian
# =============================================================================

# Default token storage location
TOKEN_FILE = Path.home() / ".atlassian_oauth_tokens.json"

# OAuth endpoints
ATLASSIAN_AUTH_URL = "https://auth.atlassian.com/authorize"
ATLASSIAN_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ATLASSIAN_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to receive OAuth callback."""

    def do_GET(self):
        """Handle the OAuth callback."""
        query = parse_qs(urlparse(self.path).query)

        if "code" in query:
            self.server.auth_code = query["code"][0]
            self.server.auth_state = query.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1>Authorization Successful!</h1>
                <p>You can close this window and return to the terminal.</p>
                </body></html>
            """)
        elif "error" in query:
            self.server.auth_error = query.get("error_description", query["error"])[0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"""
                <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1>Authorization Failed</h1>
                <p>{self.server.auth_error}</p>
                </body></html>
            """.encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress logging."""
        pass


class AtlassianOAuth:
    """Manages OAuth 2.0 3LO authentication for Atlassian."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_port: int = 8089,
        token_file: Path = TOKEN_FILE,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = f"http://localhost:{redirect_port}/callback"
        self.redirect_port = redirect_port
        self.token_file = token_file
        self.tokens: dict = {}
        self.cloud_id: Optional[str] = None
        self.site_url: Optional[str] = None
        self._load_tokens()

    def _load_tokens(self):
        """Load tokens from file if they exist."""
        if self.token_file.exists():
            try:
                with open(self.token_file, "r") as f:
                    data = json.load(f)
                    self.tokens = data.get("tokens", {})
                    self.cloud_id = data.get("cloud_id")
                    self.site_url = data.get("site_url")
            except (json.JSONDecodeError, IOError):
                self.tokens = {}

    def _save_tokens(self):
        """Save tokens to file."""
        with open(self.token_file, "w") as f:
            json.dump(
                {
                    "tokens": self.tokens,
                    "cloud_id": self.cloud_id,
                    "site_url": self.site_url,
                },
                f,
                indent=2,
            )
        # Secure the token file
        self.token_file.chmod(0o600)

    def get_authorization_url(self, state: str) -> str:
        """Construct the OAuth authorization URL."""
        params = {
            "audience": "api.atlassian.com",
            "client_id": self.client_id,
            "scope": "read:jira-work read:jira-user search:jira read:confluence-content.all read:confluence-user read:confluence-space.summary read:me offline_access",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
        return f"{ATLASSIAN_AUTH_URL}?{urlencode(params)}"

    def authorize(self) -> bool:
        """Run the OAuth authorization flow."""
        state = secrets.token_urlsafe(32)

        # Start local server to receive callback
        server = HTTPServer(("localhost", self.redirect_port), OAuthCallbackHandler)
        server.auth_code = None
        server.auth_state = None
        server.auth_error = None
        server.timeout = 120  # 2 minute timeout

        # Open browser for authorization
        auth_url = self.get_authorization_url(state)
        print(f"\nOpening browser for Atlassian authorization...", file=sys.stderr)
        print(f"If the browser doesn't open, visit:\n{auth_url}\n", file=sys.stderr)
        webbrowser.open(auth_url)

        # Wait for callback
        print("Waiting for authorization (timeout: 2 minutes)...", file=sys.stderr)
        while server.auth_code is None and server.auth_error is None:
            server.handle_request()

        if server.auth_error:
            print(f"Authorization failed: {server.auth_error}", file=sys.stderr)
            return False

        if server.auth_state != state:
            print("State mismatch - possible CSRF attack", file=sys.stderr)
            return False

        # Exchange code for tokens
        return self._exchange_code(server.auth_code)

    def _exchange_code(self, code: str) -> bool:
        """Exchange authorization code for access token."""
        resp = requests.post(
            ATLASSIAN_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            headers={"Content-Type": "application/json"},
        )

        if resp.status_code != 200:
            print(f"Token exchange failed: {resp.text}", file=sys.stderr)
            return False

        self.tokens = resp.json()
        self.tokens["obtained_at"] = time.time()

        # Get accessible resources (cloud ID and site URL)
        if not self._fetch_cloud_id():
            return False

        self._save_tokens()
        print("Authorization successful! Tokens saved.", file=sys.stderr)
        return True

    def _fetch_cloud_id(self) -> bool:
        """Fetch the cloud ID for the authorized site."""
        resp = requests.get(
            ATLASSIAN_RESOURCES_URL,
            headers={"Authorization": f"Bearer {self.tokens['access_token']}"},
        )

        if resp.status_code != 200:
            print(f"Failed to fetch accessible resources: {resp.text}", file=sys.stderr)
            return False

        resources = resp.json()
        if not resources:
            print("No accessible Atlassian sites found.", file=sys.stderr)
            return False

        # Use the first site (or let user choose if multiple)
        if len(resources) > 1:
            print("\nMultiple Atlassian sites found:", file=sys.stderr)
            for i, r in enumerate(resources):
                print(f"  {i + 1}. {r['name']} ({r['url']})", file=sys.stderr)
            choice = input("Select site (1): ").strip() or "1"
            idx = int(choice) - 1
        else:
            idx = 0

        self.cloud_id = resources[idx]["id"]
        self.site_url = resources[idx]["url"]
        print(
            f"Using site: {resources[idx]['name']} ({self.site_url})", file=sys.stderr
        )
        return True

    def refresh_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if "refresh_token" not in self.tokens:
            print("No refresh token available. Please re-authorize.", file=sys.stderr)
            return False

        resp = requests.post(
            ATLASSIAN_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.tokens["refresh_token"],
            },
            headers={"Content-Type": "application/json"},
        )

        if resp.status_code != 200:
            print(f"Token refresh failed: {resp.text}", file=sys.stderr)
            return False

        new_tokens = resp.json()
        self.tokens["access_token"] = new_tokens["access_token"]
        if "refresh_token" in new_tokens:
            self.tokens["refresh_token"] = new_tokens["refresh_token"]
        self.tokens["obtained_at"] = time.time()
        self._save_tokens()
        return True

    def get_access_token(self) -> Optional[str]:
        """Get a valid access token, refreshing if necessary."""
        if not self.tokens.get("access_token"):
            return None

        # Check if token is expired (with 60 second buffer)
        obtained_at = self.tokens.get("obtained_at", 0)
        expires_in = self.tokens.get("expires_in", 3600)
        if time.time() > obtained_at + expires_in - 60:
            if not self.refresh_token():
                return None

        return self.tokens["access_token"]

    def is_authorized(self) -> bool:
        """Check if we have valid tokens."""
        return self.get_access_token() is not None


# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class JiraMetrics:
    issues_assigned: list[dict] = field(default_factory=list)
    comments_made: list[dict] = field(default_factory=list)


@dataclass
class ConfluenceMetrics:
    pages_created: list[dict] = field(default_factory=list)
    pages_edited: list[dict] = field(default_factory=list)
    comments_made: list[dict] = field(default_factory=list)


@dataclass
class GitHubMetrics:
    prs_opened: list[dict] = field(default_factory=list)
    prs_merged: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)


@dataclass
class PersonReport:
    name: str
    date_range: tuple[str, str]
    jira: JiraMetrics = field(default_factory=JiraMetrics)
    confluence: ConfluenceMetrics = field(default_factory=ConfluenceMetrics)
    github: GitHubMetrics = field(default_factory=GitHubMetrics)


# =============================================================================
# API Clients
# =============================================================================


class AtlassianClient:
    """Client for Jira and Confluence REST APIs with OAuth 2.0 support."""

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
        elif endpoint.startswith("wiki/"):
            # Confluence endpoint
            return f"https://api.atlassian.com/ex/confluence/{self.cloud_id}/{endpoint}"
        else:
            # Default to site URL
            return urljoin(self.site_url + "/", endpoint)

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make a request with retry logic and automatic token refresh."""
        url = self._get_api_url(endpoint)

        for attempt in range(3):
            try:
                # Get fresh access token
                access_token = self.oauth.get_access_token()
                if not access_token:
                    raise requests.exceptions.HTTPError(
                        "No valid access token. Please run with --auth to authorize."
                    )

                self.session.headers["Authorization"] = f"Bearer {access_token}"
                resp = self.session.request(method, url, **kwargs)

                if resp.status_code == 401:
                    # Try to refresh token
                    if self.oauth.refresh_token():
                        continue
                    service = "Confluence" if "/wiki/" in endpoint else "Jira"
                    raise requests.exceptions.HTTPError(
                        f"{service} authentication failed (401 Unauthorized).\n"
                        f"Please run with --auth to re-authorize."
                    )
                if resp.status_code == 429:  # Rate limited
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    print(f"  Rate limited, waiting {retry_after}s...", file=sys.stderr)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return resp.json() if resp.content else {}
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    raise
                print(
                    f"  Request failed, retrying ({attempt + 1}/3)...", file=sys.stderr
                )
                time.sleep(2**attempt)
        return {}

    def get(self, endpoint: str, **kwargs) -> dict:
        return self._request("GET", endpoint, **kwargs)


class JiraClient(AtlassianClient):
    """Jira-specific API client."""

    def __init__(self, oauth: AtlassianOAuth):
        super().__init__(oauth=oauth)
        self._account_id_cache: dict[str, str] = {}

    def get_account_id(self, username: str) -> Optional[str]:
        """Look up account ID from username/email."""
        if username in self._account_id_cache:
            return self._account_id_cache[username]

        # Try searching for the user
        try:
            resp = self.get("/rest/api/3/user/search", params={"query": username})
            if resp and len(resp) > 0:
                account_id = resp[0].get("accountId")
                self._account_id_cache[username] = account_id
                return account_id
        except Exception as e:
            print(
                f"  Warning: Could not look up account ID for {username}: {e}",
                file=sys.stderr,
            )

        # Fall back to using the username as-is (might be an account ID already)
        return username

    def search_issues(self, jql: str, fields: list[str] | None = None) -> list[dict]:
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
                "customfield_10053",  # Story points
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
                "/rest/api/3/search/jql",
                json=params,
            )
            issues = resp.get("issues", [])
            all_issues.extend(issues)

            next_page_token = resp.get("nextPageToken")
            if not next_page_token:
                break

        return all_issues

    def get_issues_assigned(
        self, username: str, start_date: str, end_date: str
    ) -> list[dict]:
        """Get issues created and assigned to user in date range."""
        account_id = self.get_account_id(username)
        jql = f'assignee = "{account_id}" AND created >= "{
            start_date
        }" AND created <= "{end_date}" AND status NOT IN ("Cancelled", "Dismissed")'
        issues = self.search_issues(jql)
        return [self._format_issue(i) for i in issues]

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
            jql = f'issueFunction in commented("by {account_id}") AND updated >= "{
                start_date
            }" AND updated <= "{end_date}"'
            issues = self.search_issues(jql, fields=["key", "summary"])
            return [
                {"issue_key": i["key"], "issue_summary": i["fields"].get("summary", "")}
                for i in issues
            ]
        except Exception:
            # issueFunction might not be available, return empty
            return []

    def _extract_text_from_adf(self, adf: dict | None) -> str:
        """Extract plain text from Atlassian Document Format (ADF)."""
        if not adf:
            return ""
        if isinstance(adf, str):
            return adf

        texts = []

        def extract(node):
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

    def _format_issue(self, issue: dict) -> dict:
        """Format issue for display."""
        fields = issue.get("fields", {})
        story_points = fields.get("customfield_10053")
        description = self._extract_text_from_adf(fields.get("description"))
        return {
            "key": issue.get("key"),
            "summary": fields.get("summary", ""),
            "description": description,
            "status": fields.get("status", {}).get("name", ""),
            "type": fields.get("issuetype", {}).get("name", ""),
            "created": fields.get("created", "")[:10] if fields.get("created") else "",
            "resolved": fields.get("resolved", "")[:10]
            if fields.get("resolved")
            else "",
            "story_points": int(story_points) if story_points is not None else None,
            "url": f"{self.site_url}/browse/{issue.get('key')}",
        }


class ConfluenceClient(AtlassianClient):
    """Confluence-specific API client."""

    def __init__(self, oauth: AtlassianOAuth):
        super().__init__(oauth=oauth)

    def search_content(self, cql: str, limit: int = 100) -> list[dict]:
        """Search content using CQL."""
        all_results = []
        start = 0

        while True:
            resp = self.get(
                "/wiki/rest/api/content/search",
                params={
                    "cql": cql,
                    "start": start,
                    "limit": limit,
                    "expand": "space,version",
                },
            )
            results = resp.get("results", [])
            all_results.extend(results)

            # Check if there are more results
            if len(results) < limit:
                break
            start += limit

        return all_results

    def get_pages_created(
        self, username: str, start_date: str, end_date: str
    ) -> list[dict]:
        """Get pages created by user in date range."""
        cql = f'creator = "{username}" AND created >= "{start_date}" AND created <= "{
            end_date
        }" AND type = page'
        pages = self.search_content(cql)
        return [self._format_page(p) for p in pages]

    def get_pages_edited(
        self, username: str, start_date: str, end_date: str
    ) -> list[dict]:
        """Get pages edited by user in date range."""
        cql = f'contributor = "{username}" AND lastmodified >= "{
            start_date
        }" AND lastmodified <= "{end_date}" AND type = page'
        pages = self.search_content(cql)
        return [self._format_page(p) for p in pages]

    def get_comments_made(
        self, username: str, start_date: str, end_date: str
    ) -> list[dict]:
        """Get comments made by user in date range."""
        cql = f'creator = "{username}" AND created >= "{start_date}" AND created <= "{
            end_date
        }" AND type = comment'
        comments = self.search_content(cql)
        return [
            {"id": c.get("id"), "title": c.get("title", "Comment")} for c in comments
        ]

    def _format_page(self, page: dict) -> dict:
        """Format page for display."""
        space = page.get("space", {})
        version = page.get("version", {})
        return {
            "id": page.get("id"),
            "title": page.get("title", ""),
            "space": space.get("name", ""),
            "space_key": space.get("key", ""),
            "last_modified": version.get("when", "")[:10]
            if version.get("when")
            else "",
            "url": f"{self.site_url}/wiki{page.get('_links', {}).get('webui', '')}",
        }


class GitHubClient:
    """GitHub API client using GraphQL and REST."""

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
        self.rest_url = "https://api.github.com"

    def _graphql(self, query: str, variables: dict = None) -> dict:
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

    def _search_issues(self, query: str) -> list[dict]:
        """Search issues/PRs using REST API."""
        all_items = []
        page = 1
        per_page = 100

        while True:
            resp = self.session.get(
                f"{self.rest_url}/search/issues",
                params={
                    "q": query,
                    "per_page": per_page,
                    "page": page,
                },
            )
            if resp.status_code == 401:
                raise requests.exceptions.HTTPError(
                    f"GitHub authentication failed. Please check your GITHUB_TOKEN:\n"
                    f"  - Ensure the token is valid and not expired\n"
                    f"  - Token needs 'repo' scope for private repos or 'public_repo' for public repos\n"
                    f"  - For org searches, token may need 'read:org' scope"
                )
            if resp.status_code == 403:
                reset_time = int(
                    resp.headers.get("X-RateLimit-Reset", time.time() + 60)
                )
                wait_time = max(reset_time - time.time(), 0) + 1
                print(f"  Rate limited, waiting {int(wait_time)}s...", file=sys.stderr)
                time.sleep(wait_time)
                continue
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            all_items.extend(items)

            if len(items) < per_page:
                break
            page += 1

        return all_items

    def get_prs_opened(
        self, username: str, start_date: str, end_date: str, org: Optional[str] = None
    ) -> list[dict]:
        """Get PRs opened by user in date range."""
        query = f"is:pr author:{username} created:{start_date}..{end_date}"
        if org:
            query += f" org:{org}"
        prs = self._search_issues(query)
        return [self._format_pr(pr) for pr in prs]

    def _get_two_years_ago(self) -> str:
        """Get date string for 2 years ago."""
        two_years_ago = datetime.now().replace(year=datetime.now().year - 2)
        return two_years_ago.strftime("%Y-%m-%d")

    def get_prs_merged(
        self, username: str, start_date: str, end_date: str, org: Optional[str] = None
    ) -> list[dict]:
        """Get PRs merged by user in date range."""
        two_years_ago = self._get_two_years_ago()
        query = f"is:pr is:merged author:{username} merged:{start_date}..{end_date} created:>{two_years_ago}"
        if org:
            query += f" org:{org}"
        prs = self._search_issues(query)
        return [self._format_pr(pr) for pr in prs]

    def get_reviews(
        self, username: str, start_date: str, end_date: str, org: Optional[str] = None
    ) -> list[dict]:
        """Get PRs reviewed by user in date range."""
        two_years_ago = self._get_two_years_ago()
        query = f"is:pr reviewed-by:{username} updated:{start_date}..{end_date} created:>{two_years_ago}"
        if org:
            query += f" org:{org}"
        prs = self._search_issues(query)
        return [self._format_pr(pr) for pr in prs]

    def _format_pr(self, pr: dict) -> dict:
        """Format PR for display."""
        repo_url = pr.get("repository_url", "")
        repo_name = "/".join(repo_url.split("/")[-2:]) if repo_url else ""
        return {
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "description": pr.get("body", "") or "",
            "state": pr.get("state", ""),
            "repo": repo_name,
            "created_at": pr.get("created_at", "")[:10] if pr.get("created_at") else "",
            "merged_at": pr.get("pull_request", {}).get("merged_at", "")[:10]
            if pr.get("pull_request", {}).get("merged_at")
            else "",
            "url": pr.get("html_url", ""),
        }


# =============================================================================
# HTML Template
# =============================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Activity Report - {{ name }}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-primary: #ffffff;
            --bg-secondary: #f8f9fa;
            --text-primary: #212529;
            --text-secondary: #6c757d;
            --border-color: #dee2e6;
            --accent-jira: #0052cc;
            --accent-confluence: #1868db;
            --accent-github: #24292f;
            --success: #28a745;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: var(--text-primary);
            background: var(--bg-secondary);
            padding: 20px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
        }

        header {
            background: var(--bg-primary);
            border-radius: 12px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        header h1 {
            font-size: 2em;
            margin-bottom: 5px;
        }

        header .date-range {
            color: var(--text-secondary);
            font-size: 1.1em;
        }

        header .generated {
            color: var(--text-secondary);
            font-size: 0.9em;
            margin-top: 10px;
        }

        .summary-cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .card {
            background: var(--bg-primary);
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        .card.jira { border-top: 4px solid var(--accent-jira); }
        .card.confluence { border-top: 4px solid var(--accent-confluence); }
        .card.github { border-top: 4px solid var(--accent-github); }

        .card .metric {
            font-size: 2.5em;
            font-weight: bold;
            color: var(--text-primary);
        }

        .card .label {
            color: var(--text-secondary);
            font-size: 0.9em;
            margin-top: 5px;
        }

        .charts-section {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }

        .chart-container {
            background: var(--bg-primary);
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        .chart-container h3 {
            margin-bottom: 15px;
            font-size: 1.1em;
        }

        section.details {
            background: var(--bg-primary);
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        section.details h2 {
            font-size: 1.3em;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid var(--border-color);
        }

        section.details h2.jira { color: var(--accent-jira); }
        section.details h2.confluence { color: var(--accent-confluence); }
        section.details h2.github { color: var(--accent-github); }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }

        th, td {
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }

        th {
            background: var(--bg-secondary);
            font-weight: 600;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        tr:hover {
            background: var(--bg-secondary);
        }

        a {
            color: var(--accent-jira);
            text-decoration: none;
        }

        a:hover {
            text-decoration: underline;
        }

        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 0.8em;
            font-weight: 500;
        }

        .badge-success { background: #d4edda; color: #155724; }
        .badge-info { background: #d1ecf1; color: #0c5460; }
        .badge-warning { background: #fff3cd; color: #856404; }

        .empty-state {
            color: var(--text-secondary);
            font-style: italic;
            padding: 20px;
            text-align: center;
        }

        @media (max-width: 768px) {
            .charts-section {
                grid-template-columns: 1fr;
            }

            table {
                font-size: 0.9em;
            }

            th, td {
                padding: 8px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Activity Report: {{ name }}</h1>
            <div class="date-range">{{ start_date }} to {{ end_date }}</div>
            <div class="generated">Generated on {{ generated_at }}</div>
        </header>

        <div class="summary-cards">
            <div class="card jira">
                <div class="metric">{{ jira.issues_assigned | length }}</div>
                <div class="label">Jira Issues Assigned</div>
            </div>
            <div class="card confluence">
                <div class="metric">{{ confluence.pages_created | length }}</div>
                <div class="label">Confluence Pages Created</div>
            </div>
            <div class="card confluence">
                <div class="metric">{{ confluence.pages_edited | length }}</div>
                <div class="label">Pages Edited</div>
            </div>
            <div class="card github">
                <div class="metric">{{ github.prs_merged | length }}</div>
                <div class="label">PRs Merged</div>
            </div>
            <div class="card github">
                <div class="metric">{{ github.reviews | length }}</div>
                <div class="label">Code Reviews</div>
            </div>
        </div>

        <div class="charts-section">
            <div class="chart-container">
                <h3>Activity Distribution</h3>
                <canvas id="distributionChart"></canvas>
            </div>
            <div class="chart-container">
                <h3>GitHub Activity by Week</h3>
                <canvas id="githubChart"></canvas>
            </div>
        </div>

        <section class="details">
            <h2 class="jira">Jira Activity</h2>

            <h4>Issues Assigned ({{ jira.issues_assigned | length }})</h4>
            {% if jira.issues_assigned %}
            <table>
                <thead>
                    <tr>
                        <th>Key</th>
                        <th>Summary</th>
                        <th>Type</th>
                        <th>Status</th>
                        <th>Story Points</th>
                        <th>Created</th>
                    </tr>
                </thead>
                <tbody>
                    {% for issue in jira.issues_assigned %}
                    <tr>
                        <td><a href="{{ issue.url }}" target="_blank">{{ issue.key }}</a></td>
                        <td>{{ issue.summary[:60] }}{% if issue.summary|length > 60 %}...{% endif %}</td>
                        <td>{{ issue.type }}</td>
                        <td><span class="badge badge-info">{{ issue.status }}</span></td>
                        <td>{{ issue.story_points if issue.story_points is not none else '-' }}</td>
                        <td>{{ issue.created }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p class="empty-state">No new issues assigned in this period</p>
            {% endif %}
        </section>

        <section class="details">
            <h2 class="confluence">Confluence Activity</h2>

            <h4>Pages Created ({{ confluence.pages_created | length }})</h4>
            {% if confluence.pages_created %}
            <table>
                <thead>
                    <tr>
                        <th>Title</th>
                        <th>Space</th>
                        <th>Last Modified</th>
                    </tr>
                </thead>
                <tbody>
                    {% for page in confluence.pages_created %}
                    <tr>
                        <td><a href="{{ page.url }}" target="_blank">{{ page.title }}</a></td>
                        <td>{{ page.space }}</td>
                        <td>{{ page.last_modified }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p class="empty-state">No pages created in this period</p>
            {% endif %}

            <h4>Pages Edited ({{ confluence.pages_edited | length }})</h4>
            {% if confluence.pages_edited %}
            <table>
                <thead>
                    <tr>
                        <th>Title</th>
                        <th>Space</th>
                        <th>Last Modified</th>
                    </tr>
                </thead>
                <tbody>
                    {% for page in confluence.pages_edited %}
                    <tr>
                        <td><a href="{{ page.url }}" target="_blank">{{ page.title }}</a></td>
                        <td>{{ page.space }}</td>
                        <td>{{ page.last_modified }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p class="empty-state">No pages edited in this period</p>
            {% endif %}
        </section>

        <section class="details">
            <h2 class="github">GitHub Activity</h2>

            <h4>Pull Requests Merged ({{ github.prs_merged | length }})</h4>
            {% if github.prs_merged %}
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Title</th>
                        <th>Repository</th>
                        <th>Merged</th>
                    </tr>
                </thead>
                <tbody>
                    {% for pr in github.prs_merged %}
                    <tr>
                        <td><a href="{{ pr.url }}" target="_blank">#{{ pr.number }}</a></td>
                        <td>{{ pr.title[:60] }}{% if pr.title|length > 60 %}...{% endif %}</td>
                        <td>{{ pr.repo }}</td>
                        <td>{{ pr.merged_at or pr.created_at }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p class="empty-state">No PRs merged in this period</p>
            {% endif %}

            <h4>Pull Requests Opened ({{ github.prs_opened | length }})</h4>
            {% if github.prs_opened %}
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Title</th>
                        <th>Repository</th>
                        <th>Created</th>
                    </tr>
                </thead>
                <tbody>
                    {% for pr in github.prs_opened %}
                    <tr>
                        <td><a href="{{ pr.url }}" target="_blank">#{{ pr.number }}</a></td>
                        <td>{{ pr.title[:60] }}{% if pr.title|length > 60 %}...{% endif %}</td>
                        <td>{{ pr.repo }}</td>
                        <td>{{ pr.created_at }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p class="empty-state">No PRs opened in this period</p>
            {% endif %}

            <h4>Code Reviews ({{ github.reviews | length }})</h4>
            {% if github.reviews %}
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Title</th>
                        <th>Repository</th>
                        <th>Date</th>
                    </tr>
                </thead>
                <tbody>
                    {% for pr in github.reviews %}
                    <tr>
                        <td><a href="{{ pr.url }}" target="_blank">#{{ pr.number }}</a></td>
                        <td>{{ pr.title[:60] }}{% if pr.title|length > 60 %}...{% endif %}</td>
                        <td>{{ pr.repo }}</td>
                        <td>{{ pr.created_at }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p class="empty-state">No code reviews in this period</p>
            {% endif %}
        </section>
    </div>

    <script>
        // Activity Distribution Chart
        const distCtx = document.getElementById('distributionChart').getContext('2d');
        new Chart(distCtx, {
            type: 'doughnut',
            data: {
                labels: ['Jira Issues', 'Confluence Pages', 'GitHub PRs', 'Code Reviews'],
                datasets: [{
                    data: [
                        {{ jira.issues_assigned | length }},
                        {{ (confluence.pages_created | length) + (confluence.pages_edited | length) }},
                        {{ (github.prs_opened | length) + (github.prs_merged | length) }},
                        {{ github.reviews | length }}
                    ],
                    backgroundColor: ['#0052cc', '#1868db', '#24292f', '#6f42c1']
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });

        // GitHub Activity Chart (Weekly)
        const ghCtx = document.getElementById('githubChart').getContext('2d');
        new Chart(ghCtx, {
            type: 'line',
            data: {
                labels: {{ week_labels | tojson }},
                datasets: [
                    {
                        label: 'PRs Opened',
                        data: {{ prs_opened_by_week | tojson }},
                        borderColor: '#0366d6',
                        backgroundColor: 'rgba(3, 102, 214, 0.1)',
                        tension: 0.1,
                        fill: false
                    },
                    {
                        label: 'PRs Merged',
                        data: {{ prs_merged_by_week | tojson }},
                        borderColor: '#28a745',
                        backgroundColor: 'rgba(40, 167, 69, 0.1)',
                        tension: 0.1,
                        fill: false
                    },
                    {
                        label: 'Reviews',
                        data: {{ reviews_by_week | tojson }},
                        borderColor: '#6f42c1',
                        backgroundColor: 'rgba(111, 66, 193, 0.1)',
                        tension: 0.1,
                        fill: false
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            stepSize: 1
                        }
                    }
                }
            }
        });
    </script>
</body>
</html>
"""


# =============================================================================
# Report Generator
# =============================================================================


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


def generate_report(report: PersonReport, output_dir: str) -> str:
    """Generate HTML report for a person."""
    template = Template(HTML_TEMPLATE)

    # Compute weekly GitHub activity data for chart
    start_date, end_date = report.date_range
    week_labels = get_week_labels(start_date, end_date)
    prs_opened_by_week = aggregate_by_week(
        report.github.prs_opened, "created_at", start_date, end_date
    )
    prs_merged_by_week = aggregate_by_week(
        report.github.prs_merged, "merged_at", start_date, end_date
    )
    reviews_by_week = aggregate_by_week(
        report.github.reviews, "created_at", start_date, end_date
    )

    html = template.render(
        name=report.name,
        start_date=report.date_range[0],
        end_date=report.date_range[1],
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        jira=report.jira,
        confluence=report.confluence,
        github=report.github,
        week_labels=week_labels,
        prs_opened_by_week=prs_opened_by_week,
        prs_merged_by_week=prs_merged_by_week,
        reviews_by_week=reviews_by_week,
    )

    # Create filename
    safe_name = report.name.lower().replace(" ", "_")
    filename = f"{safe_name}_{report.date_range[0]}_{report.date_range[1]}.html"
    filepath = os.path.join(output_dir, filename)

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    return filepath


def export_to_csv(report: PersonReport, output_dir: str) -> list[str]:
    """Export report data to CSV files."""
    os.makedirs(output_dir, exist_ok=True)
    safe_name = report.name.lower().replace(" ", "_")
    start, end = report.date_range
    csv_files = []

    # Jira issues CSV
    if report.jira.issues_assigned:
        jira_filename = f"{safe_name}_jira_issues_{start}_{end}.csv"
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

    # Confluence pages CSV (created + edited combined)
    confluence_pages = []
    for page in report.confluence.pages_created:
        confluence_pages.append({**page, "activity_type": "created"})
    for page in report.confluence.pages_edited:
        confluence_pages.append({**page, "activity_type": "edited"})

    if confluence_pages:
        confluence_filename = f"{safe_name}_confluence_pages_{start}_{end}.csv"
        confluence_filepath = os.path.join(output_dir, confluence_filename)
        with open(confluence_filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "title",
                    "space",
                    "activity_type",
                    "last_modified",
                    "url",
                ],
            )
            writer.writeheader()
            for page in confluence_pages:
                writer.writerow({
                    "title": page.get("title", ""),
                    "space": page.get("space", ""),
                    "activity_type": page.get("activity_type", ""),
                    "last_modified": page.get("last_modified", ""),
                    "url": page.get("url", ""),
                })
        csv_files.append(confluence_filepath)

    # GitHub activity CSV (PRs opened + merged + reviews combined)
    github_activity = []
    for pr in report.github.prs_opened:
        github_activity.append({
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "description": pr.get("description", ""),
            "activity_type": "pr_opened",
            "repo": pr.get("repo", ""),
            "date": pr.get("created_at", ""),
            "url": pr.get("url", ""),
        })
    for pr in report.github.prs_merged:
        github_activity.append({
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "description": pr.get("description", ""),
            "activity_type": "pr_merged",
            "repo": pr.get("repo", ""),
            "date": pr.get("merged_at") or pr.get("created_at", ""),
            "url": pr.get("url", ""),
        })
    for pr in report.github.reviews:
        github_activity.append({
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "description": pr.get("description", ""),
            "activity_type": "review",
            "repo": pr.get("repo", ""),
            "date": pr.get("created_at", ""),
            "url": pr.get("url", ""),
        })

    if github_activity:
        github_filename = f"{safe_name}_github_activity_{start}_{end}.csv"
        github_filepath = os.path.join(output_dir, github_filename)
        with open(github_filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["number", "title", "description", "activity_type", "repo", "date", "url"],
            )
            writer.writeheader()
            writer.writerows(github_activity)
        csv_files.append(github_filepath)

    return csv_files


# =============================================================================
# Main CLI
# =============================================================================


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
def main(
    config: Optional[str],
    start: Optional[str],
    end: Optional[str],
    output: str,
    github_org: Optional[str],
    auth: bool,
):
    """Generate activity reports for team members across Jira, Confluence, and GitHub."""

    # Get OAuth client
    oauth = get_oauth_client()

    # Handle authorization flow
    if auth:
        print("Starting Atlassian OAuth authorization flow...", file=sys.stderr)
        if oauth.authorize():
            print("\nAuthorization complete! You can now run reports.", file=sys.stderr)
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
    print(f"Loading configuration from {config}...", file=sys.stderr)
    cfg = load_config(config)

    # Validate GitHub token
    github_token = validate_github_token()

    # Initialize clients
    print("Initializing API clients...", file=sys.stderr)
    print(f"Using Atlassian site: {oauth.site_url}", file=sys.stderr)
    jira_client = JiraClient(oauth)
    confluence_client = ConfluenceClient(oauth)
    github_client = GitHubClient(github_token)

    # Process each team member
    reports_generated = []
    csvs_generated = []

    for member in cfg["team"]:
        name = member["name"]
        jira_user = member.get("jira_username", "")
        confluence_user = member.get("confluence_username", jira_user)
        github_user = member.get("github_username", "")

        print(f"\nProcessing {name}...", file=sys.stderr)

        report = PersonReport(
            name=name,
            date_range=(start, end),
        )

        # Fetch Jira data
        if jira_user:
            try:
                print(f"  Fetching Jira data for {jira_user}...", file=sys.stderr)
                report.jira.issues_assigned = jira_client.get_issues_assigned(
                    jira_user, start, end
                )
                report.jira.comments_made = jira_client.get_comments_made(
                    jira_user, start, end
                )
            except Exception as e:
                print(f"  Warning: Error fetching Jira data: {e}", file=sys.stderr)

        # Fetch Confluence data
        if confluence_user:
            try:
                print(
                    f"  Fetching Confluence data for {confluence_user}...",
                    file=sys.stderr,
                )
                report.confluence.pages_created = confluence_client.get_pages_created(
                    confluence_user, start, end
                )
                report.confluence.pages_edited = confluence_client.get_pages_edited(
                    confluence_user, start, end
                )
                report.confluence.comments_made = confluence_client.get_comments_made(
                    confluence_user, start, end
                )
            except Exception as e:
                print(
                    f"  Warning: Error fetching Confluence data: {e}", file=sys.stderr
                )

        # Fetch GitHub data
        if github_user:
            try:
                print(f"  Fetching GitHub data for {github_user}...", file=sys.stderr)
                report.github.prs_opened = github_client.get_prs_opened(
                    github_user, start, end, github_org
                )
                report.github.prs_merged = github_client.get_prs_merged(
                    github_user, start, end, github_org
                )
                report.github.reviews = github_client.get_reviews(
                    github_user, start, end, github_org
                )
            except Exception as e:
                print(f"  Warning: Error fetching GitHub data: {e}", file=sys.stderr)

        # Export to CSV
        try:
            csv_files = export_to_csv(report, output)
            csvs_generated.extend(csv_files)
            for csv_file in csv_files:
                print(f"  Exported: {csv_file}", file=sys.stderr)
        except Exception as e:
            print(f"  Error exporting CSV: {e}", file=sys.stderr)

        # Generate report
        try:
            filepath = generate_report(report, output)
            reports_generated.append(filepath)
            print(f"  Generated: {filepath}", file=sys.stderr)
        except Exception as e:
            print(f"  Error generating report: {e}", file=sys.stderr)

    # Summary
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"Generated {len(csvs_generated)} CSV file(s):", file=sys.stderr)
    for path in csvs_generated:
        print(f"  - {path}", file=sys.stderr)
    print(f"\nGenerated {len(reports_generated)} HTML report(s):", file=sys.stderr)
    for path in reports_generated:
        print(f"  - {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
