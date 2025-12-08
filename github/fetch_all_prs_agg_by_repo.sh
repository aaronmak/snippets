#!/bin/bash

# Fetch all PRs authored by me (or involving me) in a given org within a date range
# with pagination, then aggregate by repository

usage() {
	echo "Usage: $0 [-c|--contributions] <org> <created_date_start> <created_date_end>"
	echo ""
	echo "Options:"
	echo "  -c, --contributions  Include PRs where you authored, commented, were mentioned, or reviewed"
	exit 1
}

# Parse options
mode="author"
while [[ $# -gt 0 ]]; do
	case $1 in
	-c | --contributions)
		mode="contributions"
		shift
		;;
	-*)
		echo "Unknown option: $1"
		usage
		;;
	*)
		break
		;;
	esac
done

# Parse positional arguments
org="${1:?$(usage)}"
created_date_start="${2:?$(usage)}"
created_date_end="${3:?$(usage)}"

# Function to fetch PRs using search API with pagination
fetch_prs_by_search() {
	local search_query="$1"
	local results="[]"
	local cursor=""

	while true; do
		if [ -z "$cursor" ]; then
			response=$(gh api graphql -f query='
        query($searchQuery: String!) {
          search(query: $searchQuery, type: ISSUE, first: 100) {
            nodes {
              ... on PullRequest {
                url
                repository { nameWithOwner }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }' -f searchQuery="$search_query")
		else
			response=$(gh api graphql -f query='
        query($searchQuery: String!, $cursor: String!) {
          search(query: $searchQuery, type: ISSUE, first: 100, after: $cursor) {
            nodes {
              ... on PullRequest {
                url
                repository { nameWithOwner }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }' -f searchQuery="$search_query" -f cursor="$cursor")
		fi

		nodes=$(echo "$response" | jq '.data.search.nodes')
		results=$(echo "$results $nodes" | jq -s 'add')

		has_next=$(echo "$response" | jq -r '.data.search.pageInfo.hasNextPage')
		cursor=$(echo "$response" | jq -r '.data.search.pageInfo.endCursor')

		echo "Fetched $(echo "$nodes" | jq 'length') PRs, total so far: $(echo "$results" | jq 'length')" >&2

		if [ "$has_next" != "true" ]; then
			break
		fi
	done

	echo "$results"
}

# Function to fetch contributions using multiple search queries
fetch_contributions() {
	local org="$1"
	local date_start="$2"
	local date_end="$3"
	local all_results="[]"

	# Fetch PRs authored by me
	echo "Fetching PRs authored by @me..." >&2
	local author_results
	author_results=$(fetch_prs_by_search "is:pr author:@me org:${org} created:${date_start}..${date_end}")
	all_results=$(echo "$all_results $author_results" | jq -s 'add')

	# Fetch PRs where I commented
	echo "Fetching PRs where @me commented..." >&2
	local commenter_results
	commenter_results=$(fetch_prs_by_search "is:pr commenter:@me org:${org} created:${date_start}..${date_end}")
	all_results=$(echo "$all_results $commenter_results" | jq -s 'add')

	# Fetch PRs where I was mentioned
	echo "Fetching PRs where @me was mentioned..." >&2
	local mentions_results
	mentions_results=$(fetch_prs_by_search "is:pr mentions:@me org:${org} created:${date_start}..${date_end}")
	all_results=$(echo "$all_results $mentions_results" | jq -s 'add')

	# Fetch PRs I reviewed
	echo "Fetching PRs reviewed by @me..." >&2
	local reviewed_results
	reviewed_results=$(fetch_prs_by_search "is:pr reviewed-by:@me org:${org} created:${date_start}..${date_end}")
	all_results=$(echo "$all_results $reviewed_results" | jq -s 'add')

	# Deduplicate by URL
	echo "$all_results" | jq 'unique_by(.url)'
}

# Main execution
if [ "$mode" = "contributions" ]; then
	echo "Fetching contributions (author, commenter, mentions, reviewed-by)..." >&2
	results=$(fetch_contributions "$org" "$created_date_start" "$created_date_end")
else
	echo "Fetching PRs authored by @me..." >&2
	search_query="is:pr author:@me org:${org} created:${created_date_start}..${created_date_end}"
	results=$(fetch_prs_by_search "$search_query")
fi

# Aggregate by repository and output
echo "$results" | jq '
  [.[].repository.nameWithOwner]
  | group_by(.)
  | map({repo: .[0], count: length})
  | sort_by(-.count)
'
