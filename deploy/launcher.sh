#!/bin/bash
# The deploy of the report site, installed by root as /srv/benchmark-report/deploy/deploy.sh and
# otherwise never changed: find the newest commit of main whose Test workflow passed, asking GitHub
# rather than trusting the webhook (so a forged trigger can only redeploy tested code), and run that
# commit's deploy/deploy.sh, which does the rest. Runs as benchdeploy.
set -u
REPO=madsuite-org/benchmark-report
B=${DEPLOY_ROOT:-/srv/benchmark-report}; C=$B/code; PIN=$B/deploy/PINNED
newest() {
  curl -sS -m 30 "https://api.github.com/repos/$REPO/actions/workflows/test.yml/runs?branch=main&event=push&status=success&per_page=1" \
    | python3 -c 'import json,sys; r=json.load(sys.stdin)["workflow_runs"]; print(r[0]["head_sha"] if r else "")'
}
old=$(cat "$PIN" 2>/dev/null || true)
# the webhook comes as the run ends, and GitHub's list of runs can take minutes to show it: wait for
# a newer commit for up to 4 min (once, on a re-run after a deploy)
tries=24; [ -n "${DEPLOY_QUICK:-}" ] && tries=1
for i in $(seq "$tries"); do
  sha=$(newest)
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] && [ "$sha" != "$old" ] && break
  [ "$i" -lt "$tries" ] && sleep 10
done
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "no tested commit found"; exit 1; }
[ "$sha" = "$old" ] && { echo "already at $sha"; exit 0; }
git -C "$C" fetch -q origin main || exit 1
git -C "$C" merge-base --is-ancestor "$sha" origin/main || { echo "$sha is not on main"; exit 1; }
script=$(mktemp) || exit 1
trap 'rm -f "$script"' EXIT
git -C "$C" show "$sha:deploy/deploy.sh" > "$script" || { echo "$sha has no deploy/deploy.sh"; exit 1; }
DEPLOY_SHA=$sha DEPLOY_LAUNCHER=$0 bash "$script"
