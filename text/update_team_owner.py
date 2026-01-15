#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "ruamel.yaml",
# ]
# ///
"""
Update meta.owner.team field in all YAML files within a directory.
Ignores dbt_project.yml files.

Usage:
    uv run update_team_owner.py -d <directory> -t <team_name>
    uv run update_team_owner.py -d <directory> -t <team_name> -f <old_team> [-f <old_team2> ...]
    uv run update_team_owner.py -d <directory> -t <team_name> --only-missing
"""

import argparse
import sys
from pathlib import Path

from ruamel.yaml import YAML


def update_model_owner_team(
    model_config: dict,
    team_name: str,
    from_teams: set[str] | None = None,
    only_missing: bool = False,
) -> bool:
    """
    Update meta.owner.team for a single model config.
    If from_teams is provided, only replace if current team is in that set.
    If only_missing is True, only add the team if it doesn't exist.
    Returns True if any changes were made.
    """
    if not isinstance(model_config, dict):
        return False

    changed = False

    if "meta" in model_config and isinstance(model_config["meta"], dict):
        meta = model_config["meta"]
        if "owner" in meta:
            if isinstance(meta["owner"], dict):
                current_team = meta["owner"].get("team")
                if current_team is None:
                    meta["owner"]["team"] = team_name
                    changed = True
                elif not only_missing and current_team != team_name:
                    if from_teams is None or current_team in from_teams:
                        meta["owner"]["team"] = team_name
                        changed = True
            else:
                if from_teams is None and not only_missing:
                    meta["owner"] = {"team": team_name}
                    changed = True
        else:
            if from_teams is None:
                meta["owner"] = {"team": team_name}
                changed = True

    return changed


def process_models_recursively(
    config: dict | list,
    team_name: str,
    from_teams: set[str] | None = None,
    only_missing: bool = False,
) -> bool:
    """
    Recursively process models configuration.
    Handles nested model definitions (e.g., models organized by folder/schema).
    Returns True if any changes were made.
    """
    if isinstance(config, list):
        changed = False
        for item in config:
            if update_model_owner_team(item, team_name, from_teams, only_missing):
                changed = True
            if isinstance(item, dict):
                for key, value in item.items():
                    if isinstance(value, (dict, list)) and key != "meta":
                        if process_models_recursively(
                            value, team_name, from_teams, only_missing
                        ):
                            changed = True
        return changed
    elif isinstance(config, dict):
        changed = False
        for key, value in config.items():
            if isinstance(value, (dict, list)):
                if process_models_recursively(
                    value, team_name, from_teams, only_missing
                ):
                    changed = True
        return changed
    return False


def process_yaml_file(
    file_path: Path,
    team_name: str,
    dry_run: bool = False,
    from_teams: set[str] | None = None,
    only_missing: bool = False,
) -> bool:
    """
    Process a single YAML file, updating meta.owner.team in models.
    Returns True if changes were made (or would be made in dry-run mode).
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 80
    yaml.indent(mapping=2, sequence=4, offset=2)

    try:
        with open(file_path, "r") as f:
            data = yaml.load(f)
    except Exception as e:
        print(f"  Warning: Could not parse {file_path}: {e}")
        return False

    if data is None or "models" not in data:
        return False

    changed = process_models_recursively(
        data["models"], team_name, from_teams, only_missing
    )

    if changed and not dry_run:
        with open(file_path, "w") as f:
            yaml.dump(data, f)

    return changed


def find_yaml_files(directory: Path) -> list[Path]:
    """Find all YAML files in directory, excluding dbt_project.yml."""
    yaml_files = []
    for pattern in ["**/*.yml", "**/*.yaml"]:
        for file_path in directory.glob(pattern):
            if file_path.name == "dbt_project.yml":
                continue
            yaml_files.append(file_path)
    return sorted(yaml_files)


def main():
    parser = argparse.ArgumentParser(
        description="Update meta.owner.team field in YAML files"
    )
    parser.add_argument(
        "-d",
        "--directory",
        type=Path,
        required=True,
        help="Directory to search for YAML files",
    )
    parser.add_argument(
        "-t",
        "--team",
        type=str,
        required=True,
        help="Team name to set in meta.owner.team",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without modifying files",
    )
    parser.add_argument(
        "-f",
        "--from-team",
        type=str,
        action="append",
        dest="from_teams",
        help="Only replace if current team matches this value (can be specified multiple times)",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Only add team if meta.owner.team is missing (don't overwrite existing values)",
    )

    args = parser.parse_args()

    if not args.directory.is_dir():
        print(f"Error: {args.directory} is not a valid directory")
        sys.exit(1)

    yaml_files = find_yaml_files(args.directory)

    if not yaml_files:
        print(f"No YAML files found in {args.directory}")
        sys.exit(0)

    print(f"Found {len(yaml_files)} YAML files")
    if args.dry_run:
        print("(Dry run mode - no files will be modified)\n")

    from_teams = set(args.from_teams) if args.from_teams else None

    modified_count = 0
    for file_path in yaml_files:
        changed = process_yaml_file(
            file_path, args.team, args.dry_run, from_teams, args.only_missing
        )
        if changed:
            modified_count += 1
            status = "Would update" if args.dry_run else "Updated"
            print(f"  {status}: {file_path}")

    print(f"\n{'Would modify' if args.dry_run else 'Modified'} {modified_count} files")


if __name__ == "__main__":
    main()
