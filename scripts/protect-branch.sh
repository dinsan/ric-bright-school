#!/usr/bin/env bash
# Apply .github/rulesets/protect-main.json to the GitHub repository, so a pull request
# cannot merge into the default branch unless the "CI passed" check succeeded.
# Run once by a repository admin (`make protect-branch`); re-running updates the ruleset.
#
#   scripts/protect-branch.sh [OWNER/REPO]      default: the repo of the current checkout
#
# Needs the GitHub CLI (https://cli.github.com), logged in with admin rights: gh auth login
set -euo pipefail
cd "$(dirname "$0")/.."

RULESET=.github/rulesets/protect-main.json

command -v gh >/dev/null 2>&1 \
    || { echo "The GitHub CLI 'gh' is required: https://cli.github.com" >&2; exit 1; }
gh auth status >/dev/null 2>&1 \
    || { echo "Log in first: gh auth login" >&2; exit 1; }

repo="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
name=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["name"])' "$RULESET")

existing_id=$(gh api "repos/$repo/rulesets" --paginate \
    --jq ".[] | select(.name == \"$name\") | .id")

if [ -n "$existing_id" ]; then
    gh api --method PUT "repos/$repo/rulesets/$existing_id" --input "$RULESET" >/dev/null
    echo "Updated ruleset '$name' (id $existing_id) on $repo."
else
    gh api --method POST "repos/$repo/rulesets" --input "$RULESET" >/dev/null
    echo "Created ruleset '$name' on $repo."
fi
echo "Review it at https://github.com/$repo/settings/rules"
