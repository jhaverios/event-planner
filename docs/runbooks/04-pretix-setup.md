# Runbook 04 — Pretix setup, and the core loop proven

Every call below was executed against a real instance and the responses are quoted.
Nothing here is theoretical.

## What this proves

| Requirement | Proven by |
|---|---|
| Admin creates an event with date, time, location | Event series plus a subevent per date |
| Broker registers a client by name and mobile | One order, one position, four question answers |
| Broker attribution | `broker_code` answer on every order |
| Client gets a unique QR | Ticket PDF, 32-character opaque secret |
| Staff scan at the door | Check-in accepted, duplicate rejected |
| Live attendance count | Check-in list status endpoint |
| Attended versus no-show per broker | Report built from positions and their check-ins |

## Order of operations

### 1. Admin user

`pretix createsuperuser` is interactive. To script it:

```bash
docker compose exec -T pretix python3 -m pretix shell -c "
from pretix.base.models import User
u, _ = User.objects.get_or_create(email='admin@jslwealth.in',
                                  defaults={'is_staff': True, 'is_active': True})
u.is_staff = True; u.set_password('CHANGEME'); u.save()"
```

### 2. Organizer and teams

Permissions are **not** `can_*` booleans. Current Pretix uses `all_event_permissions`
plus a `limit_event_permissions` JSON map. Valid names, read from the running instance:

| Scope | Group | Actions |
|---|---|---|
| Event | `event` | cancel |
| Event | `event.items` | write |
| Event | `event.orders` | checkin, read, write |
| Event | `event.settings.general` | write |
| Event | `event.settings.invoicing` / `.payment` / `.tax` | write |
| Event | `event.subevents` | write |
| Event | `event.vouchers` | read, write |
| Organizer | `organizer.events` | create |
| Organizer | `organizer.devices` | read, write |
| Organizer | `organizer.teams` | write |
| Organizer | `organizer.customers` / `.giftcards` / `.reusablemedia` | read, write |
| Organizer | `organizer.settings.general` / `.seatingplans` | write |
| Organizer | `organizer.outgoingmails` | read |

Three teams, least privilege:

- **Admins** — `all_event_permissions=True`, `all_organizer_permissions=True`.
- **Automation** — the token n8n uses. Events: orders read, write and checkin; items
  write; subevents write; general settings write; vouchers read and write. Organizer:
  events create, devices write. Nothing else.
- **Door staff** — orders read and checkin only. Cannot change an order, cannot see
  settings, cannot create anything.

The API token lives on the team, not the user:

```python
from pretix.base.models.organizer import TeamAPIToken
TeamAPIToken.objects.create(team=automation, name='n8n', active=True).token
```

### 3. Event series

Each real event is a **subevent** of one series, so questions, products and settings are
defined once.

```http
POST /api/v1/organizers/jsl/events/
{"name":{"en":"JSL Investor Events"},"slug":"investor-events","live":false,
 "testmode":false,"currency":"INR","date_from":"2026-10-01T18:30:00+05:30",
 "is_public":false,"has_subevents":true}
```

**Gotcha.** `"live": true` on creation is rejected: *"Events cannot be created as 'live'.
Quotas and payment must be added to the event before sales can go live."* Create it
unpublished, add the quota, then set live in a second call.

**Keep `testmode` false.** pretixSCAN refuses to validate tickets created while the shop
is in test mode, and the failure looks like a broken scanner at the door.

### 4. Product and questions

```http
POST .../events/investor-events/items/
{"name":{"en":"Registration"},"default_price":"0.00","admission":true,
 "active":true,"personalized":true}
```

Four questions, all attached to that item:

| Identifier | Type | Required | Hidden | Purpose |
|---|---|---|---|---|
| `client_phone` | `TEL` | yes | no | The client's mobile. `TEL` is a native phone type. |
| `broker_code` | `S` | no | yes | Broker attribution. Hidden means backend only. |
| `consent_given` | `B` | no | yes | The broker's attestation that the client agreed. |
| `consent_version` | `S` | no | yes | Which notice text was shown, so consent is auditable. |

### 5. Event date, quota, check-in list

```http
POST .../subevents/     # meta_data is REQUIRED; send {} if you have none
POST .../quotas/        {"name":"…","size":500,"items":[1],"subevent":1}
POST .../checkinlists/  {"name":"Main entrance","all_products":true,"subevent":1}
```

**Gotcha.** Omitting `meta_data` returns `400 {"meta_data": ["This field is required."]}`.

### 6. A broker registers a client

```http
POST .../orders/
{"status":"p","payment_provider":"free","locale":"en","send_email":false,
 "positions":[{"positionid":1,"item":1,"price":"0.00",
   "attendee_name":"Rohan Mehta","subevent":1,
   "answers":[{"question":1,"answer":"+919876543210","options":[]},
              {"question":2,"answer":"BRK042","options":[]},
              {"question":3,"answer":"true","options":[]},
              {"question":4,"answer":"notice-v1-2026-09-21","options":[]}]}]}
```

Returns `201` with the order code and a 32-character `secret` on the position. That secret
is the QR.

`send_email` false because the client is contacted over WhatsApp, not email. There is no
top-level `phone` field on order creation, which is why the mobile rides as a question.

### 7. The ticket PDF

Enable the plugin once per event:

```http
PATCH .../events/investor-events/  {"plugins":["pretix.plugins.ticketoutputpdf"]}
GET   .../orderpositions/{id}/download/pdf/
```

**Gotcha, and it matters for automation.** The first request returns **409** while the PDF
renders, the next returns **200**. Observed exactly that. Any workflow fetching a ticket
must retry rather than treat 409 as failure.

### 8. The door

```http
POST /api/v1/organizers/jsl/checkinrpc/redeem/
{"secret":"<32 chars>","lists":[1],"source_type":"barcode","type":"entry"}
```

| Scan | Response |
|---|---|
| First | `201`, `status: ok` |
| Same ticket again | `400`, `status: error`, `reason: already_redeemed` |

Duplicate protection is server-side, so two tablets on the same door cannot double-admit.

### 9. Live attendance and the per-broker report

`GET .../checkinlists/{id}/status/` returns `position_count` and `checkin_count`.

For the report, read positions with their check-ins and group by the `broker_code` answer.
A position with a non-empty `checkins` array attended; one without it, after the event
ended, is a no-show. Real output from four registrations across two brokers:

```
broker         registered  attended  no-show   rate
BRK042                  2         1        1    50%
BRK107                  2         0        2     0%
TOTAL                   4         1        3    25%
```

That grouping is also what drives the post-event WhatsApp split: attendees get the thank
you, no-shows get the alternative message.

## What the QR actually contains

Decoded from the rendered PDF rather than assumed:

```
payload : 5d3jxvtbp7fngu8nt9u6shzsh8w7ayqj
length  : 32
equals the ticket secret : yes
contains first name / surname / phone / broker code / order code : no
```

So a photographed or forwarded QR leaks nothing. Scanning resolves to the person
server-side. The printed ticket **text** does show the attendee name and order code, which
is correct for a document the client holds.

## Credentials

The API token belongs to the Automation team and is written to
`deploy/.admin-credentials`, which is gitignored and mode 600. Rotate it by setting
`active=False` on the old `TeamAPIToken` and creating a new one; nothing else changes.
