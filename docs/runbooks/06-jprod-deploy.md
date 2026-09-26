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

---

## Proving it works

`scripts/smoke-test.py` tests a deployment against the thing people actually use. Two modes,
because one of them sends real messages to a real phone.

**Safe to run at any time**, sends nothing:

```bash
cd ~/event-planner && set -a && . deploy/.env && set +a
python3 scripts/smoke-test.py --base https://events.jslwealth.in --quick
```

Checks health (including that the pretix token actually authenticates, not merely that it is
set), every auth boundary, the event list, all six inputs the form must reject, and the door's
behaviour on a reference that does not exist.

**The full path**, which registers a real client and sends to a phone and address you own:

```bash
python3 scripts/smoke-test.py --base https://events.jslwealth.in \
    --phone 9833693876 --email you@example.com --broker SMOKE01
```

Adds: registration accepted, the speaker present in the line the client is actually sent, both
channels accepted, the door admitting the guest once and refusing the same pass twice, the
numbers reaching the dashboard attributed to the right broker, another broker being unable to
see that registration, and the test order cancelled afterwards so it does not inflate turnout.

37 checks. A single failure exits non-zero and is listed again at the end.

### Two bugs it found on its first run

**`cancel_order` used the wrong URL.** `POST /orders/{code}/cancel/` returns 404; the endpoint
is `mark_canceled` — American spelling, one L. `/mark_cancelled/` and `/delete/` are also 404.
This mattered beyond the test: clearing test registrations before an event is exactly what that
function is for, and it would have failed quietly.

**The registration reply did not say what had been sent.** It returned the bare event name while
the client received the name plus the speaker, so nothing in the response could be checked
against what actually went out. It now returns `event_line` as well.

### A third, found running it against the live site

The test signed its single HTTP client in as an administrator before asserting how the site
treats a stranger. Both anonymous checks failed — on the cookie the test was carrying, not on
anything the server did. A test that authenticates itself and then asks "is this closed to the
public?" cannot answer the question.

Now `c` is anonymous for its whole life, privileged calls go through a second client, and the
password block runs on a throwaway — which means signing out is really asserted rather than
skipped whenever a password was supplied.

Cancelling could not work remotely either. The pretix client on the machine running the test
talks to a *different* pretix than the one under test, so cleanup either failed or would have
cancelled an unrelated order that happened to share a five-character code. It cancels through
the server being tested now, which is what `/api/admin/cancel` is for.

## The door on the day

Two ways in, and the second one exists because venue wifi is not a thing to bet on.

### pretixSCAN on Android — the primary

The app syncs the whole guest list while it has signal, then **scans offline**. Check-ins queue
on the phone and upload when signal returns, so a dead wifi router slows the dashboard, not the
queue.

Pairing is the one action our API token cannot do — pretix returns 403, because a device is an
organizer-level object. So it is a click-through, once per station:

1. `https://tickets.jslwealth.in/control/login` — the credentials are on jprod, and the file is
   root-owned because the deploy ran as root:

   ```bash
   sudo grep PRETIX_ADMIN ~/event-planner/deploy/.env
   ```

   The password is generated fresh by `deploy-jprod.sh` on each machine, so a copy from anywhere
   else is the wrong one.
2. **Organizer `jsl` → Devices → Add device.**
3. Name it for the gate — "Entrance 1", "Entrance 2". The name is stamped on every check-in, so
   afterwards you can tell which door admitted whom.
4. **Limit to events** → tick `JSL Investor Events`. A device scoped to one event cannot be
   walked to another one. Leave **Gate** blank; gates are for venues where several physical
   entrances feed one list.
5. **Security profile — choose plain `pretixSCAN`.** There are four, and only one is right:

   | Option | Verdict |
   |---|---|
   | Full device access | reads *and changes* orders and gift cards. A door scanner has no business doing either, and a lost phone should not carry that |
   | **pretixSCAN** | **this one.** Syncs the guest list to the device, so it keeps admitting people when the venue wifi dies, and allows search for the guest whose QR will not read |
   | pretixSCAN (online only, no order sync) | every scan needs live connectivity. At a hotel, with two hundred people arriving at once, this is the setting that strands the door |
   | pretixSCAN (kiosk mode, no order sync, no search) | no search, so there is no answer for a smudged printout or a deleted WhatsApp. That case is exactly why a human is standing there |

6. Save. Pretix shows a pairing code. Install pretixSCAN from the Play Store, enter the code
   once, and the phone syncs.

**Two stations for 200 guests.** Not for throughput — a single phone handles 200 scans easily —
but so that one flat battery or one frozen app does not stop the entrance.

pretixSCAN is an Android app. Whether pretix ships an official iOS scanner is **not something I
have confirmed**; if the door staff are on iPhones, use the web console below, which works in
Safari.

### `/door` — the backup, and the typed-reference path

A signed link, deliberately short-lived:

```bash
python3 portal/issue-link.py door entrance-1 --days 7
```

It does two jobs pretixSCAN does not do as well: it runs on any phone with a browser, and it
takes a **typed five-character reference** when a QR will not read — a cracked screen, a
printout that smudged, a client who deleted the WhatsApp. Same check-in list, same rules: a pass
already used comes back `already_redeemed`.

It needs signal. That is the trade, and it is why it is the backup rather than the primary.

### Undoing a registration

`/admin` → the event → **Remove** on the person's row, behind a confirm. It cancels the order in
pretix, so the pass stops working at the door, and drops the delivery record so the "WhatsApp
sent" count stops counting a message to a guest who no longer exists.

Use it for test registrations and for a broker who typed the wrong client. It cannot be undone
from the dashboard — re-register the person instead, which sends them a fresh pass.


## The day-before reminder

`jsl_event_v3` says *"your registration is confirmed"*, which is the wrong sentence on the 23rd.
`jsl_event_reminder_v1` says *"we look forward to seeing you tomorrow"* and carries the same six
parameters, so `wati.send_pass` takes a template name rather than a second sender existing.

It runs inside the portal container, where the credentials and the dependencies already are —
the same reason `bootstrap-pretix.py` runs inside pretix:

```bash
cd ~/event-planner/deploy

# who WOULD get it, and why anyone is being skipped. Sends nothing.
sudo docker compose exec -T portal python - --subevent 2 < ~/event-planner/scripts/send-reminders.py

# prove it on one person first
sudo docker compose exec -T portal python - --subevent 2 --limit 1 --send < ~/event-planner/scripts/send-reminders.py

# then the rest
sudo docker compose exec -T portal python - --subevent 2 --send < ~/event-planner/scripts/send-reminders.py
```

Dry run is the default. The flag that messages two hundred people is the one you have to type.

### Sending twice is the failure that matters

So it is guarded three ways, and the third is the one worth understanding:

1. the run skips anyone already recorded as sent
2. a partial unique index refuses a second successful row, so two overlapping runs cannot both
   pass the check above and both write
3. **if the "who was already sent" read fails, the run aborts.** Returning an empty set would
   have meant "nobody has been reminded" and messaged the entire list a second time. A failure
   to read is not the same as nothing to read, and treating them alike is how a mailing list
   sends twice

Failures stay retryable: only successful sends are unique, so re-running picks up whoever the
first pass could not reach.

### Who gets skipped, and why

- cancelled or expired orders, read from the order's status rather than the position's flag
- anyone with no mobile number on their registration
- a WATI contact with `allowBroadcast` off, or deleted. Looked up per number through
  `getContacts?name=<phone>`, which matches the phone as well as the saved name — confirmed
  against a contact stored as "Jeet Jhaveri" and found by number alone. There are sixteen
  thousand contacts on the account, so this has to be a lookup, never a scan

A lookup that *fails* is treated as allowed rather than blocked: a WATI hiccup must not silently
withhold a reminder someone is expecting. The send is the real gate — WATI answers 200 with
`result: false` when it refuses.

## The post-event follow-up

A second message after the event, carrying the session document and a quick-reply button, so the
desk gets a list of warm names instead of reading fifty inboxes.

### Why it is not sent from WATI's campaign builder

That account holds **16,229 contacts**, and the campaign builder picks its audience from a
dropdown. One wrong selection sends a marketing message from a regulated firm to all of them —
a compliance incident, not an embarrassment. The audience for a post-event follow-up is knowable
exactly: the people whose pass was scanned. So it is derived, not selected.

### The template

| Field | Value |
|---|---|
| Name | `jsl_investment_event_followup` |
| Category | **Marketing** — not Utility |
| Header | Document, a fixed URL under `/assets` |
| Buttons | Quick reply: `I'm Interested` |
| Body | `{{1}}` event · `{{2}}` date · `{{3}}` topic |

**Marketing is not a choice.** "Explore whether it suits your investment goals" is lead
generation and Meta classifies it that way whatever is selected. The consequence that matters:
a marketing template is withheld from anyone who has opted out of marketing, and nothing in the
send result says who. The Utility reminders reached everyone; this one will not.

**The document is a fixed URL, not a variable.** `{{qr_url}}` earned its place because every
guest needed a different image. Here everyone gets the same PDF, so a variable buys nothing but
an unverified path. Put the file in `portal/static/assets/`, rebuild, and confirm before
submitting — Meta fetches the URL during review, and a header link that 404s fails the whole
message rather than just the attachment:

```bash
curl -sI https://events.jslwealth.in/assets/<file>.pdf | head -3   # wants 200
```

### Sending it

```bash
cd ~/event-planner && git pull --no-edit && cd deploy
sudo docker compose up -d --build portal && sleep 10

V='Event name|24 September 2026|what was discussed'

# 1. dry run — attendees only, sends nothing
sudo docker compose exec -T portal python - --subevent 1 \
  --template jsl_investment_event_followup --attended --values "$V" \
  < ~/event-planner/scripts/send-reminders.py

# 2. one person, on a handset you hold
#    …same command plus --limit 1 --send
# 3. the rest
#    …same command plus --send
```

`--attended` is what restricts it to people who came. Without it the message goes to no-shows
too, thanking them for attending something they missed.

The value count is checked against what WATI stores for the template before anything is sent,
because a mismatch is rejected per message rather than once.

### Reading who is interested

```bash
sudo docker compose exec -T portal python - --subevent 1 --since 2026-09-26 \
  < ~/event-planner/scripts/read-interest.py
```

Growth has no webhooks, so replies are polled. The script does **not** filter for the event type
of an inbound quick reply, because no one on this account had ever replied when it was written
and that value has never been observed. It reports everything on the conversation that is not
outbound and prints the type it finds — so the first real reply names the thing in the output
instead of being dropped by a filter built on a guess. Once that type is known, it can be
filtered properly.

It ends with a CSV of phone, name, reference and **the broker who invited them**, so each lead
goes back to the person who owns the relationship.

### The list comes from pretix, never from WATI

WATI stores a contact's name as **the phone number** until told otherwise — contact
`917990040687` on this account has `fullName: "917990040687"`. Any personalisation drawn from
WATI's own contact attributes would greet real clients by their number. Pretix holds the name the
broker typed, which is why `export-attendees.py` exists.
