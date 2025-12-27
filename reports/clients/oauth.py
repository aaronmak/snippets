"""OAuth 2.0 3LO authentication for Atlassian."""

import json
import secrets
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, parse_qs, urlparse

import requests


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
