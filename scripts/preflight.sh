#!/usr/bin/env bash
# Read-only survey of the target server. Changes nothing, installs nothing.
# Run on jprod, paste the output back.
#
#   curl -fsSL https://raw.githubusercontent.com/jhaverios/event-planner/main/scripts/preflight.sh | bash
# or, if the repo is already cloned:
#   bash scripts/preflight.sh

set -uo pipefail
line() { printf '\n=== %s ===\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

printf 'event-planner preflight  %s  on %s\n' "$(date -Is)" "$(hostname)"

line "OS and kernel"
( . /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" ) || echo "unknown"
uname -srm
echo "ipv6: $( [ -e /proc/net/if_inet6 ] && echo yes || echo 'NO — use docker-compose.no-ipv6.yml' )"

line "CPU and memory"
echo "cores: $(nproc 2>/dev/null || echo '?')"
free -h 2>/dev/null | sed -n '1,2p' || echo "free unavailable"

line "Disk"
df -h / /var/lib/docker 2>/dev/null | sort -u

line "Docker"
if have docker; then
  docker --version
  docker compose version 2>/dev/null || echo "compose plugin MISSING"
  docker info --format 'storage driver: {{.Driver}} | containers: {{.Containers}} | images: {{.Images}}' 2>&1 | head -1
else
  echo "docker MISSING — install it before deploying"
fi

line "What already listens on 80 and 443"
if have ss; then ss -lntp 2>/dev/null | awk 'NR==1 || /:80 |:443 /'
elif have netstat; then netstat -lntp 2>/dev/null | awk 'NR<3 || /:80 |:443 /'
else echo "no ss or netstat"; fi
for svc in nginx apache2 httpd caddy traefik; do
  if have "$svc" || systemctl is-active --quiet "$svc" 2>/dev/null; then
    echo "found: $svc ($(systemctl is-active "$svc" 2>/dev/null || echo 'not under systemd'))"
  fi
done

line "Existing sites (so we do not collide)"
ls -1 /etc/nginx/sites-enabled/ 2>/dev/null || echo "no /etc/nginx/sites-enabled"
ls -1 /etc/nginx/conf.d/*.conf 2>/dev/null | xargs -r -n1 basename || true

line "Running containers"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null || echo "none or docker unavailable"

line "Public address"
curl -s --max-time 8 https://checkip.amazonaws.com 2>/dev/null || echo "could not determine"

line "Ports reachable from outside"
echo "Check in the EC2 console that the security group allows inbound 80 and 443 from 0.0.0.0/0."
echo "Do NOT open 5678 or 8345; those stay on loopback behind the proxy."

line "Summary"
echo "Paste everything above. The two answers that decide the deploy shape are:"
echo "  1. is Docker installed?"
echo "  2. is anything already bound to 80/443?"
