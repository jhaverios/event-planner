#!/usr/bin/env bash
# Pull WhatsApp replies onto the admin dashboard. Safe to run on a schedule.
#
# WATI's Growth plan has no webhooks, so nothing pushes a reply to us and the
# dashboard would otherwise only be as fresh as the last time somebody
# remembered to run the poller. This is that somebody.
#
#   */30 * * * * /home/ubuntu/event-planner/scripts/poll-interest.sh 1 \
#       >> /var/log/jsl-interest.log 2>&1
#
# An every-N-minutes schedule needs no timezone conversion, which is the one
# way a cron entry on this box (UTC, while everyone here thinks in IST) would
# otherwise go quietly wrong.
#
# People already recorded as interested are not polled again, so the call count
# falls as replies come in rather than staying flat.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBEVENT="${1:-1}"
cd "$ROOT/deploy"
printf '\n===== %s =====\n' "$(date -Is)"
# --template matters: each person's own send time is the cut-off for what
# counts as a reply. Without it the poller counted every inbound message a
# contact had ever sent, and most of these are clients of many years.
exec docker compose exec -T portal python - --subevent "$SUBEVENT" --quiet \
    --template "${2:-jsl_investment_event_followup}" \
    < "$ROOT/scripts/read-interest.py"
