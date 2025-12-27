#!/usr/bin/env python3
"""
Multi-Platform Activity Report Generator

Generates individual HTML reports summarizing activity across Jira and GitHub
for multiple team members.

This module re-exports from the reports package for backwards compatibility.
See pyproject.toml for dependencies.
"""

from cli import main

if __name__ == "__main__":
    main()
