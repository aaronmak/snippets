# Activity Report Generator

Generate HTML activity reports summarizing Jira and GitHub activity for team members.

## Features

- **Jira Integration**: Issues assigned, resolved, and comments made
- **GitHub Integration**: PRs opened, merged, and code reviews
- **AI Summaries**: Optional monthly summaries powered by Claude
- **Visualizations**: Interactive charts showing weekly activity trends
- **Exports**: HTML reports with date filtering + CSV exports

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

## Setup

### 1. Create Atlassian OAuth 2.0 App

1. Go to https://developer.atlassian.com/console/myapps/
2. Create OAuth 2.0 integration
3. Set callback URL: `http://localhost:8089/callback`
4. Enable scopes: `read:jira-work`, `read:jira-user`, `search:jira`, `read:me`, `offline_access`

### 2. Create GitHub Token

1. Go to https://github.com/settings/tokens
2. Create token with `repo` and `read:org` scopes

### 3. Set Environment Variables

```bash
export ATLASSIAN_CLIENT_ID="your-oauth-client-id"
export ATLASSIAN_CLIENT_SECRET="your-oauth-client-secret"
export GITHUB_TOKEN="ghp_your_github_token"

# Optional: for AI summaries
export ANTHROPIC_API_KEY="your-anthropic-key"
```

### 4. Create Config File

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your team members
```

### 5. Authorize with Atlassian (one-time)

```bash
uv run activity_report.py --auth
```

## Usage

```bash
# Generate reports for a date range
uv run activity_report.py -c config.yaml -s 2024-01-01 -e 2024-12-31

# With AI-powered monthly summaries
uv run activity_report.py -c config.yaml -s 2024-01-01 -e 2024-12-31 --ai-summary

# Filter to specific GitHub org
uv run activity_report.py -c config.yaml -s 2024-01-01 -e 2024-12-31 --github-org myorg

# Custom output directory
uv run activity_report.py -c config.yaml -s 2024-01-01 -e 2024-12-31 -o ./my-reports
```

## Output

Reports are saved to the `output/` directory (or custom path via `-o`):

- `{name}_{start}_{end}.html` - Interactive HTML report
- `{name}_jira_issues_assigned_{start}_{end}.csv` - Jira issues CSV
- `{name}_jira_issues_resolved_{start}_{end}.csv` - Resolved issues CSV
- `{name}_github_activity_{start}_{end}.csv` - GitHub activity CSV

## Config Format

```yaml
team:
  - name: "John Doe"
    jira_username: "jdoe"        # Jira account ID or email
    github_username: "johndoe"   # GitHub username
```
