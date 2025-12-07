#!/bin/bash

# Fetch all PRs authored by me in a given org within a date range
# with pagination, then aggregate by repository

# Parse arguments
org="${1:?Usage: $0 <org> <created_date_start> <created_date_end>}"
created_date_start="${2:?Usage: $0 <org> <created_date_start> <created_date_end>}"
created_date_end="${3:?Usage: $0 <org> <created_date_start> <created_date_end>}"

search_query="is:pr author:@me org:${org} created:${created_date_start}..${created_date_end}"

results="[]"
cursor=""

while true; do
	if [ -z "$cursor" ]; then
		response=$(gh api graphql -f query='
      query($searchQuery: String!) {
        search(
          query: $searchQuery
          type: ISSUE
          first: 100
        ) {
          issueCount
          nodes {
            ... on PullRequest {
              repository {
                nameWithOwner
              }
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }' -f searchQuery="$search_query")
	else
		response=$(gh api graphql -f query='
      query($searchQuery: String!, $cursor: String!) {
        search(
          query: $searchQuery
          type: ISSUE
          first: 100
          after: $cursor
        ) {
          issueCount
          nodes {
            ... on PullRequest {
              repository {
                nameWithOwner
              }
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }' -f searchQuery="$search_query" -f cursor="$cursor")
	fi

	# Extract nodes and append to results
	nodes=$(echo "$response" | jq '.data.search.nodes')
	results=$(echo "$results $nodes" | jq -s 'add')

	# Check for next page
	has_next=$(echo "$response" | jq -r '.data.search.pageInfo.hasNextPage')
	cursor=$(echo "$response" | jq -r '.data.search.pageInfo.endCursor')

	echo "Fetched $(echo "$nodes" | jq 'length') PRs, total so far: $(echo "$results" | jq 'length')" >&2

	if [ "$has_next" != "true" ]; then
		break
	fi
done

# Aggregate by repository and output
echo "$results" | jq '
  [.[].repository.nameWithOwner]
  | group_by(.)
  | map({repo: .[0], count: length})
  | sort_by(-.count)
'
