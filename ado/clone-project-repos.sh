#!/usr/bin/env bash
# Clone (or update) all Git repositories in an Azure DevOps project.
#
# Usage:
#   ./clone-project-repos.sh -o <organization> -p <project> [-d <dest-dir>] [--ssh] [--update]
#
# Auth (HTTPS):
#   - AZURE_DEVOPS_PAT env var (PAT needs "Code (Read)" scope)
#
# Requires: git, curl, jq

set -euo pipefail

ORG=""
PROJECT=""
DEST=""
USE_SSH=false
UPDATE=false

usage() {
  cat <<EOF
Usage: $(basename "$0") -o <organization> -p <project> [options]

Options:
  -o, --org        Azure DevOps organization name (e.g. "contoso")
  -p, --project    Project name
  -d, --dest       Destination directory (default: ./<project>)
      --ssh        Clone using SSH URLs instead of HTTPS
      --update     Run 'git pull --ff-only' on repos that already exist
  -h, --help       Show this help

Auth: Set AZURE_DEVOPS_PAT environment variable (PAT needs "Code (Read)" scope).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -o|--org) ORG="$2"; shift 2 ;;
    -p|--project) PROJECT="$2"; shift 2 ;;
    -d|--dest) DEST="$2"; shift 2 ;;
    --ssh) USE_SSH=true; shift ;;
    --update) UPDATE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [ -z "$ORG" ] || [ -z "$PROJECT" ]; then
  usage; exit 1
fi

for cmd in git curl jq; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing required tool: $cmd" >&2; exit 1; }
done

DEST="${DEST:-./$PROJECT}"

if [ -z "${AZURE_DEVOPS_PAT:-}" ]; then
  echo "Error: AZURE_DEVOPS_PAT environment variable is not set." >&2
  echo "Please set your Azure DevOps PAT and try again." >&2
  exit 1
fi

AUTH_HEADER="Authorization: Basic $(printf ':%s' "$AZURE_DEVOPS_PAT" | base64 | tr -d '\n')"

PROJECT_ENC="$(jq -rn --arg s "$PROJECT" '$s|@uri')"
API_URL="https://dev.azure.com/${ORG}/${PROJECT_ENC}/_apis/git/repositories?api-version=7.1"

echo "Fetching repositories for ${ORG}/${PROJECT}..."
RESPONSE="$(curl -sS -f -H "$AUTH_HEADER" "$API_URL")" || {
  echo "Failed to list repositories (check org/project names and credentials)." >&2; exit 1; }

URL_FIELD="remoteUrl"
$USE_SSH && URL_FIELD="sshUrl"

REPOS="$(echo "$RESPONSE" | jq -r --arg f "$URL_FIELD" \
  '.value[] | select(.isDisabled != true) | [.name, .[$f]] | @tsv')"

if [ -z "$REPOS" ]; then
  echo "No repositories found."; exit 0
fi

mkdir -p "$DEST"
TOTAL=0; CLONED=0; UPDATED=0; SKIPPED=0; FAILED=0; FAILED_LIST=""

# Pass the auth header per-command so credentials are never written to .git/config.
git_auth() {
  if $USE_SSH; then git "$@"; else git -c "http.extraHeader=$AUTH_HEADER" "$@"; fi
}

while IFS=$'\t' read -r NAME URL; do
  [ -z "$NAME" ] && continue
  TOTAL=$((TOTAL + 1))
  TARGET="$DEST/$NAME"

  if [ -d "$TARGET/.git" ]; then
    if $UPDATE; then
      echo "==> Updating $NAME"
      if git_auth -C "$TARGET" pull --ff-only; then
        UPDATED=$((UPDATED + 1))
      else
        FAILED=$((FAILED + 1)); FAILED_LIST="$FAILED_LIST $NAME"
      fi
    else
      echo "==> Skipping $NAME (already exists)"
      SKIPPED=$((SKIPPED + 1))
    fi
    continue
  fi

  echo "==> Cloning $NAME"
  if git_auth clone "$URL" "$TARGET"; then
    CLONED=$((CLONED + 1))
  else
    FAILED=$((FAILED + 1)); FAILED_LIST="$FAILED_LIST $NAME"
  fi
done <<< "$REPOS"

echo
echo "Done. Total: $TOTAL | Cloned: $CLONED | Updated: $UPDATED | Skipped: $SKIPPED | Failed: $FAILED"
[ -n "$FAILED_LIST" ] && echo "Failed repos:$FAILED_LIST"
[ "$FAILED" -eq 0 ]
