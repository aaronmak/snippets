"""Configuration schema and validation using Pydantic."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from constants import DEFAULT_STORY_POINTS_FIELD


class TeamMember(BaseModel):
    """Configuration for a single team member."""

    name: str = Field(..., min_length=1, description="Display name for the team member")
    jira_username: Optional[str] = Field(
        None, description="Jira username or email for looking up account"
    )
    github_username: Optional[str] = Field(
        None, description="GitHub username for fetching PRs and reviews"
    )

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty or whitespace only")
        return v.strip()


class Config(BaseModel):
    """Root configuration schema."""

    team: list[TeamMember] = Field(
        ..., min_length=1, description="List of team members to generate reports for"
    )
    story_points_field: str = Field(
        DEFAULT_STORY_POINTS_FIELD,
        description="Jira custom field ID for story points",
    )

    @field_validator("team")
    @classmethod
    def team_not_empty(cls, v: list[TeamMember]) -> list[TeamMember]:
        if not v:
            raise ValueError("team must contain at least one member")
        return v
