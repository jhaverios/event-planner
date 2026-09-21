#!/usr/bin/env bash
# One-command deploy for the event portal. Safe to re-run; it never overwrites
# an existing .env or pretix.cfg, so secrets survive upgrades.
#
#   sudo bash scripts/deploy-jprod.sh
#
# Override any prompt by exporting it first, e.g.
#   PRETIX_DOMAIN=events.jslwealth.in SMTP_USER=events@jslwealth.in \
#   bash scripts/deploy-jprod.sh

set -euo pipefail
cd "$(dirname "$0")/.."
DEPLOY=deploy

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$1"; }
die()  { printf '\033[31mXX %s\033[0m\n' "$1" >&2; exit 1; }
ask()  { # ask VAR "prompt" "default"
  local v=$1 p=$2 d=${3:-}
  if [ -n "${!v:-}" ]; then return; fi
  if [ -t 0 ]; then read -r -p "$p${d:+ [$d]}: " REPLY || true; else REPLY=""; fi
  printf -v "$v" '%s' "${REPLY:-$d}"
  [ -n "${!v}" ] || die "$v is required"
}

say "Checking prerequisites"
command -v docker >/dev/null || die "Docker is not installed. Install Docker Engine and the compose plugin first."
docker compose version >/dev/null 2>&1 || die "The docker compose plugin is missing."
docker info >/dev/null 2>&1 || die "Cannot talk to the Docker daemon. Are you root, or in the docker group?"
echo "docker $(docker --version | awk '{print $3}' | tr -d ,), compose $(docker compose version --short)"

# --- Configuration -------------------------------------------------------
if [ ! -f "$DEPLOY/.env" ]; then
  say "First run: collecting configuration"
  ask PRETIX_DOMAIN "Domain for the events site" "events.jslwealth.in"
  ask N8N_DOMAIN    "Domain for the automation site" "flow.jslwealth.in"
  ask ACME_EMAIL    "Email for TLS certificate notices"
  # ZeptoMail's username is the literal string "emailapikey", which is NOT the
  # From address. Keep the two separate or pretix sends as the wrong sender.
  ask SMTP_HOST     "SMTP host" "smtp.zeptomail.com"
  ask SMTP_PORT     "SMTP port" "587"
  ask SMTP_USER     "SMTP username" "emailapikey"
  ask SMTP_PASSWORD "SMTP password (ZeptoMail send-mail token)"
  ask SMTP_FROM     "From address (must be a verified sender in ZeptoMail)" "events@jslwealth.in"

  gen() { openssl rand -hex "${1:-16}"; }
  PRETIX_DB_PASSWORD=$(gen 16)
  cat > "$DEPLOY/.env" <<EOF
PRETIX_DOMAIN=$PRETIX_DOMAIN
N8N_DOMAIN=$N8N_DOMAIN
ACME_EMAIL=$ACME_EMAIL
POSTGRES_SUPERUSER=postgres
POSTGRES_SUPERUSER_PASSWORD=$(gen 16)
PRETIX_DB_PASSWORD=$PRETIX_DB_PASSWORD
N8N_DB_PASSWORD=$(gen 16)
N8N_ENCRYPTION_KEY=$(gen 32)
N8N_PROXY_HOPS=1
N8N_EXECUTION_RETENTION_HOURS=720
N8N_SAVE_ON_SUCCESS=all
TZ=Asia/Kolkata
PRETIX_CRON_INTERVAL=900
EOF
  chmod 600 "$DEPLOY/.env"

  sed -e "s|^url=.*|url=https://$PRETIX_DOMAIN|" \
      -e "s|^password=CHANGEME|password=$PRETIX_DB_PASSWORD|" \
      -e "s|^from=.*|from=$SMTP_FROM|" \
      -e "s|^host=smtp.example.com|host=$SMTP_HOST|" \
      -e "s|^port=587|port=$SMTP_PORT|" \
      -e "s|^user=$|user=$SMTP_USER|" \
      -e "s|^password=$|password=$SMTP_PASSWORD|" \
      "$DEPLOY/pretix/pretix.cfg.example" > "$DEPLOY/pretix/pretix.cfg"
  chmod 600 "$DEPLOY/pretix/pretix.cfg"
  echo "wrote $DEPLOY/.env and $DEPLOY/pretix/pretix.cfg (both chmod 600, both gitignored)"
else
  say "Existing configuration found; leaving .env and pretix.cfg untouched"
fi
# shellcheck disable=SC1091
set -a; . "$DEPLOY/.env"; set +a

# --- Pick the compose files ---------------------------------------------
FILES=(-f docker-compose.yml)
if [ ! -e /proc/net/if_inet6 ]; then
  warn "No IPv6 on this kernel. Adding the IPv4-only nginx overlay, or pretix will serve nothing."
  FILES+=(-f docker-compose.no-ipv6.yml)
fi

# --- Edge: our own Caddy, or an existing proxy? -------------------------
# Fail safe: only claim 80/443 when we can positively prove they are free.
# jprod already serves other jslwealth.in sites, and a Caddy that grabs those
# ports would take them down.
PROFILE=()
listeners() {
  if   command -v ss      >/dev/null 2>&1; then ss      -lnt 2>/dev/null | awk 'NR>1{print $4}'
  elif command -v netstat >/dev/null 2>&1; then netstat -lnt 2>/dev/null | awk 'NR>2{print $4}'
  else return 1
  fi
}
if ! PORTS=$(listeners); then
  warn "Neither ss nor netstat is available, so I cannot tell what owns 80/443."
  warn "Not starting Caddy. If those ports really are free, re-run with: EDGE=caddy"
elif printf '%s\n' "$PORTS" | grep -qE ':(80|443)$'; then
  warn "Something already listens on 80/443. Not starting Caddy."
  warn "Point that proxy at 127.0.0.1:8345 (events) and 127.0.0.1:5678 (automation)."
  warn "A ready-made nginx server block is in deploy/reverse-proxy/nginx-snippet.conf"
else
  echo "Nothing on 80/443. Starting Caddy for automatic TLS."
  EDGE=${EDGE:-caddy}
fi
[ "${EDGE:-}" = caddy ] && PROFILE=(--profile edge)

say "Starting the stack"
cd "$DEPLOY"
docker compose "${FILES[@]}" "${PROFILE[@]}" up -d

say "Waiting for pretix"
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $PRETIX_DOMAIN" http://127.0.0.1:8345/control/login || true)
  case "$code" in
    200|302) echo "pretix is serving (HTTP $code)"; break ;;
  esac
  [ "$i" = 60 ] && { docker compose "${FILES[@]}" logs --tail=40 pretix; die "pretix did not come up"; }
  sleep 5
done

say "Done"
cat <<EOF
Events site       https://$PRETIX_DOMAIN/control/
Automation site   https://$N8N_DOMAIN/

Create the first admin user (interactive, asks for email and password):
  cd $(pwd) && docker compose ${FILES[*]} exec pretix pretix createsuperuser

Then follow docs/runbooks/04-pretix-setup.md to create the organizer,
the event series, the registration questions and the check-in list.
EOF
