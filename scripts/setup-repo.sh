#!/bin/sh
# One-time repository setup for the daily workflow. Needs the gh CLI logged in with admin rights on
# the repository. Secrets come from the environment so they never appear in this file or in history.
#   AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... [SOCRATA_APP_TOKEN=...] scripts/setup-repo.sh
set -eu
REPO="${1:-cbai96cbai/pothole}"

# Let Actions open pull requests (the repair agent's patches and the Sunday drill).
gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true

gh secret set AZURE_OPENAI_ENDPOINT --repo "$REPO" --body "$AZURE_OPENAI_ENDPOINT"
gh secret set AZURE_OPENAI_API_KEY --repo "$REPO" --body "$AZURE_OPENAI_API_KEY"
if [ -n "${SOCRATA_APP_TOKEN:-}" ]; then
  gh secret set SOCRATA_APP_TOKEN --repo "$REPO" --body "$SOCRATA_APP_TOKEN"
fi

# Nothing runs, and no model step runs, until each is switched on by hand for an observed run.
gh variable set DAILY_ENABLED --repo "$REPO" --body false
gh variable set AI_ENABLED --repo "$REPO" --body false
