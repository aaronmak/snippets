"""Constants and enums for the activity report generator."""

from enum import Enum


class JiraStatus(str, Enum):
    """Jira issue status values."""
    CLOSED = "Closed"
    RESOLVED = "Resolved"
    DONE = "Done"
    CANCELLED = "Cancelled"
    DISMISSED = "Dismissed"


class JiraIssueType(str, Enum):
    """Jira issue types."""
    EPIC = "Epic"


# Default Jira custom fields
DEFAULT_STORY_POINTS_FIELD = "customfield_10053"

# Jira API endpoints
JIRA_SEARCH_ENDPOINT = "/rest/api/3/search/jql"
JIRA_USER_SEARCH_ENDPOINT = "/rest/api/3/user/search"
JIRA_CHANGELOG_ENDPOINT = "/rest/api/3/issue/{issue_key}/changelog"

# OAuth endpoints
ATLASSIAN_AUTH_URL = "https://auth.atlassian.com/authorize"
ATLASSIAN_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ATLASSIAN_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

# GitHub GraphQL endpoint
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# Default OAuth redirect port
DEFAULT_OAUTH_PORT = 8089

# HTTP status codes
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_TOO_MANY_REQUESTS = 429

# Retry configuration
MAX_RETRIES = 3
DEFAULT_RATE_LIMIT_WAIT = 60

# Parallel request configuration
CHANGELOG_MAX_WORKERS = 10
SUMMARY_MAX_WORKERS = 4

# Cache configuration
JIRA_ACCOUNT_ID_CACHE_FILE = "~/.jira_account_id_cache.json"

# OAuth HTML templates
OAUTH_SUCCESS_HTML = b"""
<html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
<h1>Authorization Successful!</h1>
<p>You can close this window and return to the terminal.</p>
</body></html>
"""

OAUTH_ERROR_HTML_TEMPLATE = """
<html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
<h1>Authorization Failed</h1>
<p>{error}</p>
</body></html>
"""
