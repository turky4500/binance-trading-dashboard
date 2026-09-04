#!/usr/bin/env bash
# Trigger the dashboard's "Analyze & Deploy" workflow on demand.
#
# Run this from an EXTERNAL reliable scheduler (e.g. cron-job.org, UptimeRobot)
# every 5 minutes so the site keeps updating even when GitHub's own flaky
# cron scheduler skips slots.
#
# Prerequisites:
#   - GITHUB_PAT: a Personal Access Token (classic, scope: workflow)
#     https://github.com/settings/tokens
#   - ref: main
#
# Note: if a run is already in progress, GitHub answers 409 (conflict) because
# of the dashboard-cycle concurrency group. That is harmless — the scheduler
# simply tries again next cycle.
#
set -euo pipefail

OWNER="turky4500"
REPO="binance-trading-dashboard"
WORKFLOW="analyze-deploy.yml"
REF="main"
URL="https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches"

TOKEN="${GITHUB_PAT:?set GITHUB_PAT (classic, scope: workflow)}"

STATUS=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "${URL}" \
  -d "{\"ref\":\"${REF}\"}")

case "${STATUS}" in
  204) echo "dispatched ok (${STATUS})";;
  409) echo "already running (conflict ${STATUS}) — will retry next cycle";;
  *)   echo "unexpected http ${STATUS}"; exit 1;;
esac
