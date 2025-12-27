"""Data models for activity reports."""

from dataclasses import dataclass, field


@dataclass
class JiraMetrics:
    issues_assigned: list[dict] = field(default_factory=list)
    issues_resolved: list[dict] = field(default_factory=list)
    comments_made: list[dict] = field(default_factory=list)


@dataclass
class GitHubMetrics:
    prs_opened: list[dict] = field(default_factory=list)
    prs_merged: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)


@dataclass
class MonthlySummary:
    month: str  # YYYY-MM format
    month_display: str  # e.g., "January 2024"
    summary: str  # AI-generated summary


@dataclass
class PersonReport:
    name: str
    date_range: tuple[str, str]
    jira: JiraMetrics = field(default_factory=JiraMetrics)
    github: GitHubMetrics = field(default_factory=GitHubMetrics)
    monthly_summaries: list[MonthlySummary] = field(default_factory=list)
