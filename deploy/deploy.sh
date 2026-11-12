#!/bin/bash
# Deploy commit DEPLOY_SHA of main, whose Test workflow passed: run by deploy/launcher.sh (installed
# by root as /srv/benchmark-report/deploy/deploy.sh), which finds that commit and runs this file as it
# is in that commit, so changes to the deploy are tested and shipped like any other. Runs as
# benchdeploy. A trigger that arrives while a deploy runs is folded into it by the path unit, so
# this runs the launcher again after a successful deploy until nothing newer has passed (at most 3
# times).
set -u
REPO=madsuite-org/benchmark-report
B=${DEPLOY_ROOT:-/srv/benchmark-report}; C=$B/code; PIN=$B/deploy/PINNED; ST=$B/site/_deploy/status.json
SITE=$B/site/madnlp
say() { echo "$(date -Is) $*"; }
report() { printf '{"state":"%s","commit":"%s","time":"%s"}\n' "$1" "$2" "$(date -Is)" > "$ST.tmp" && mv "$ST.tmp" "$ST"; }

old=$(cat "$PIN" 2>/dev/null || true)
sha=${DEPLOY_SHA:?run by the launcher, launcher.sh}
say "deploying $sha over ${old:-nothing}"

git -C "$C" fetch -q origin main || exit 1
git -C "$C" merge-base --is-ancestor "$sha" origin/main || { say "$sha is not on main"; exit 1; }
git -C $B/archive fetch -q origin

activate() {
  local before; before=$(git -C "$C" rev-parse HEAD)
  git -C "$C" checkout -q --detach "$1" && sudo -n /usr/bin/systemctl restart benchmark-report.service || return 1
  # restarting the app drops a running job's connection, which stops the job: only when its code
  # changed, and never during a job (benchmark-queue-restart applies it when no job is running)
  git -C "$C" diff --quiet "$before" "$1" -- queue || touch "$B/deploy/queue-restart-pending"
}
ready() {
  for _ in $(seq 1 60); do
    python3 - "$SITE/version.json" "$1" <<'P' && [ "$(curl -s -m 10 -o /dev/null -w '%{http_code}' --resolve benchmark.madsuite.org:443:127.0.0.1 https://benchmark.madsuite.org/madnlp/)" = 200 ] && return 0
import json, sys
try:
    v = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
sys.exit(0 if v.get("commit") == sys.argv[2] and v.get("complete") is True else 1)
P
    sleep 5
  done
  return 1
}

if activate "$sha" && ready "$sha"; then
  echo "$sha" > "$PIN"; report ok "$sha"; say "deployed $sha"
  [ "${DEPLOY_ROUND:-1}" -lt 3 ] && DEPLOY_ROUND=$(( ${DEPLOY_ROUND:-1} + 1 )) DEPLOY_QUICK=1 exec "${DEPLOY_LAUNCHER:?}"
  exit 0
fi
say "$sha did not come up, rolling back"
report failed "$sha"
[ -n "$old" ] && activate "$old"
exit 1
