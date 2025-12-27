"""OAuth 2.0 3LO authentication for Atlassian."""

import json
import logging
import secrets
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, parse_qs, urlparse

import requests

from constants import (
    ATLASSIAN_AUTH_URL,
    ATLASSIAN_TOKEN_URL,
    ATLASSIAN_RESOURCES_URL,
    DEFAULT_OAUTH_PORT,
    OAUTH_SUCCESS_HTML,
    OAUTH_ERROR_HTML_TEMPLATE,
)

logger = logging.getLogger("activity_report")


# Default token storage location
TOKEN_FILE = Path.home() / ".atlassian_oauth_tokens.json"


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
            self.wfile.write(OAUTH_SUCCESS_HTML)
        elif "error" in query:
            self.server.auth_error = query.get("error_description", query["error"])[0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                OAUTH_ERROR_HTML_TEMPLATE.format(error=self.server.auth_error).encode()
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
        redirect_port: int = DEFAULT_OAUTH_PORT,
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
            "scope": "read:jira-work read:jira-user search:jira read:me offline_access",
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
        logger.info("Opening browser for Atlassian authorization...")
        logger.info("If the browser doesn't open, visit:\n%s", auth_url)
        webbrowser.open(auth_url)

        # Wait for callback
        logger.info("Waiting for authorization (timeout: 2 minutes)...")
        while server.auth_code is None and server.auth_error is None:
            server.handle_request()

        if server.auth_error:
            logger.error("Authorization failed: %s", server.auth_error)
            return False

        if server.auth_state != state:
            logger.error("State mismatch - possible CSRF attack")
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
            logger.error("Token exchange failed: %s", resp.text)
            return False

        self.tokens = resp.json()
        self.tokens["obtained_at"] = time.time()

        # Get accessible resources (cloud ID and site URL)
        if not self._fetch_cloud_id():
            return False

        self._save_tokens()
        logger.info("Authorization successful! Tokens saved.")
        return True

    def _fetch_cloud_id(self) -> bool:
        """Fetch the cloud ID for the authorized site."""
        resp = requests.get(
            ATLASSIAN_RESOURCES_URL,
            headers={"Authorization": f"Bearer {self.tokens['access_token']}"},
        )

        if resp.status_code != 200:
            logger.error("Failed to fetch accessible resources: %s", resp.text)
            return False

        resources = resp.json()
        if not resources:
            logger.error("No accessible Atlassian sites found.")
            return False

        # Use the first site (or let user choose if multiple)
        if len(resources) > 1:
            logger.info("Multiple Atlassian sites found:")
            for i, r in enumerate(resources):
                logger.info("  %d. %s (%s)", i + 1, r['name'], r['url'])
            choice = input("Select site (1): ").strip() or "1"
            idx = int(choice) - 1
        else:
            idx = 0

        self.cloud_id = resources[idx]["id"]
        self.site_url = resources[idx]["url"]
        logger.info("Using site: %s (%s)", resources[idx]['name'], self.site_url)
        return True

    def refresh_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if "refresh_token" not in self.tokens:
            logger.warning("No refresh token available. Please re-authorize.")
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
            logger.error("Token refresh failed: %s", resp.text)
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
