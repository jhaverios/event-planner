# Deploying to jprod

Written to be pasted, stage by stage, with the output pasted back between stages.
Nothing here is theoretical: `scripts/deploy-jprod.sh` has been run twice end to end
against a live stack, and the survey below is jprod's own output.

## What jprod is

Surveyed 2026-09-21 by `scripts/preflight.sh`, not assumed.

| | |
|---|---|
| Host | `i-0e3fdeca0fd082844`, t3.2xlarge, Ubuntu 24.04.4, 8 cores, 30 GB RAM |
| Public IP | `13.206.34.214` |
| Disk | 193 G, 69 G free |
| Docker | 29.3.0 with compose v5.1.0, daemon reachable |
| IPv6 | present, so **no** IPv4-only overlay is needed |
| Ports 80/443 | **taken by nginx**, serving atlas, clients, global, rebalance, golddesk, jhaveri-pm |
| Port 5433 | **taken** by `niyam-db-1`, so ours uses 5434 |
| Egress | WATI, ZeptoMail and the QR renderer all reachable |

**The load-bearing fact: nginx already owns 80 and 443 for six live sites.** Our Caddy must
never start. `deploy-jprod.sh` detects this and skips it, and
`deploy/reverse-proxy/nginx-snippet.conf` is the other half.

## DNS

Two A records, both to `13.206.34.214`:

| Name | Serves |
|---|---|
| `events.jslwealth.in` | the portal — brokers, admin, door |
| `tickets.jslwealth.in` | pretix and pretixSCAN |

Two names because the portal serves `/api/register` and pretix serves `/api/v1`; sharing one
hostname would mean maintaining a list of pretix URL prefixes and discovering an error at a door.

n8n gets no public name at all. It holds the API credentials for pretix, WATI and ZeptoMail and
only ever needs an administrator, so it stays on loopback:

```bash
ssh -N -L 5678:127.0.0.1:5678 ubuntu@13.206.34.214   # then http://localhost:5678
```

---

## Stage 1 — Bring the stack up

The ZeptoMail token is read with `read -s` so it does not land in shell history.

```bash
cd ~ && git clone -b claude/vibrant-bohr-nd6tg2 \
  https://github.com/jhaverios/event-planner.git
cd event-planner

export PORTAL_DOMAIN=events.jslwealth.in
export PRETIX_DOMAIN=tickets.jslwealth.in
export ACME_EMAIL=            # an address you want expiry warnings at
export SMTP_HOST=smtp.zeptomail.in
export SMTP_PORT=587
export SMTP_USER=emailapikey
export SMTP_FROM=events@jslwealth.in
read -rsp 'ZeptoMail send-mail token: ' SMTP_PASSWORD; echo; export SMTP_PASSWORD

sudo -E bash scripts/deploy-jprod.sh
```

**Expect:** the portal image builds, the directory database and schema are created, pretix
answers `HTTP 200`, and `/healthz` prints `degraded` listing `pretix_token`, `whatsapp` and
`email` as missing. Degraded is correct here — those cannot be configured yet.

**Stop and paste the output back before continuing.**

---

## Stage 2 — Set pretix up

The API token cannot exist until pretix does, which is why this stage sits in the middle.

```bash
cd ~/event-planner/deploy
sudo docker compose exec pretix pretix createsuperuser
```

Then open `http://127.0.0.1:8345/control/` through an SSH tunnel:

```bash
ssh -N -L 8345:127.0.0.1:8345 ubuntu@13.206.34.214
```

In that UI:

1. create the organizer **jsl**
2. Team settings → **API tokens** → create one named `automation` with, on the event,
   *orders read and write* and *check-in* permissions
3. copy the token

Everything after that is API work and does not need the UI.

**Paste the token back** (or keep it and run stage 3 yourself; it is prompted for, not
echoed).

---

## Stage 3 — Give the portal its credentials

`deploy-jprod.sh` tops up whatever is still missing on every run, so re-running is the
intended path rather than a workaround.

```bash
cd ~/event-planner
sudo -E bash scripts/deploy-jprod.sh
```

It prompts for the pretix API token, the WATI base URL and token, the ZeptoMail token and the
From address. Blank skips any of them and the portal reports it as missing rather than
pretending.

**Expect:** `/healthz` now prints `{"status":"ok"}`.

---

## Stage 4 — Publish it

```bash
cd ~/event-planner/deploy/reverse-proxy
sudo cp nginx-snippet.conf /etc/nginx/sites-available/event-planner
sudo ln -s /etc/nginx/sites-available/event-planner /etc/nginx/sites-enabled/
sudo nginx -t
```

**`nginx -t` must pass before the reload.** Six live sites share this nginx; a reload with a
bad config takes them all down.

```bash
sudo systemctl reload nginx
sudo certbot --nginx -d events.jslwealth.in -d tickets.jslwealth.in
```

certbot rewrites the two server blocks to add TLS and the redirect from port 80.

---

## Stage 5 — Mint the links and prove it works

```bash
cd ~/event-planner
set -a; . deploy/.env; set +a
python3 portal/issue-link.py desk  shared     --days 365
python3 portal/issue-link.py admin nimish     --days 90
python3 portal/issue-link.py door  entrance-1 --days 7
```

Then, from a phone rather than the server:

1. open the desk link, register yourself with a real mobile
2. the WhatsApp pass arrives with your own QR
3. open the door link and scan it — accepted
4. scan it again — refused
5. the admin link shows the check-in

## After it is live

Repoint QR generation off the third-party renderer and onto our own endpoint. The approved
WhatsApp template stores a bare variable for the whole URL, so this needs **no new Meta
review** — one line in `deploy/.env`, then `docker compose up -d portal`:

```
QR_BASE=https://events.jslwealth.in/webhook/ticket?f=png&t=
```

## Rolling back

The stack is confined to its own compose project and its own database. It shares nothing with
the six existing sites except nginx, and it adds one file there.

```bash
cd ~/event-planner/deploy && sudo docker compose down
sudo rm /etc/nginx/sites-enabled/event-planner
sudo nginx -t && sudo systemctl reload nginx
```
