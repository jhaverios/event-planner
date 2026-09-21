# Event Management Portal — Compose, Don't Build

## Context

`jhaverios/event-planner` is empty (no commits on any branch, no PRs). The need: JSL Wealth runs
in-person events; brokers (each with a broker code) register clients (name + Indian mobile) for an
event; the client receives a WhatsApp confirmation with a unique QR; staff scan the QR at the door;
attendance drives post-event WhatsApp follow-ups and per-broker reporting. Admins create events with a
1–2 page PDF brochure.

The user's constraint: **do not build from scratch**; compose existing open-source projects and
connectors, write only glue. Decisions the user gave this session:

| Topic | User's answer | What it means for the plan |
|---|---|---|
| WhatsApp channel | "can we use vapi?" | **No.** Vapi's own docs describe a voice-AI platform for phone/web calls; SMS only via Twilio. It cannot send WhatsApp. Plan uses Meta's WhatsApp Cloud API. Vapi stays a possible voice-reminder add-on later. |
| Meta Business status | "I don't know yet" | Assume nothing exists. Meta onboarding is the critical path, not software. |
| Hosting | "ssh jprod - we have the JSL Wealth server" | Self-host on jprod. **This session cannot reach jprod** (no `ssh` binary, no config, HTTPS-proxy-only egress). Deliverable = Docker Compose + runbooks + a read-only pre-flight script the user runs. |
| Broker UX | "Web form now, WhatsApp form later" | Phase 1: n8n web form with a signed personal link per broker. Phase 4: registration inside WhatsApp (WhatsApp Flows). |
| Consent | "Not sure, flag it in the plan" | Design for broker attestation (mandatory tick box, logged with notice version); flagged as an open compliance item. |
| jprod state | "you only check" | Cannot be checked from here; `scripts/preflight.sh` is step 1 of implementation. |
| Broker directory | CSV mapping provided; must also add a broker by name + phone ad hoc | Broker directory is a first-class table with CSV import + admin add form. Identity keyed on phone. Design for hundreds. |

## The honest verdict on "existing end-to-end repos"

Research (three agents, ~380 tool calls, official docs only) found **no production-grade open-source
project that combines event registration + QR check-in + WhatsApp automation**. Every GitHub hit that
claims all three is a student, hackathon, or weeks-old demo (0–2 stars, no tests, no releases).
Attendize is dead (last commit 2024-08), FOSSASIA Open Event Server is archived. What does exist, and is
mature, are the three building blocks below. The whole system is those three plus ~8 small n8n
workflows and configuration. There is no application codebase to write.

## Recommended architecture

```
 Admin (Pretix backend UI)            Broker (phone browser)              Staff (Android tablet)
   create event dates, brochure          signed personal link                 pretixSCAN app
            │                                   │                                   │
            ▼                                   ▼                                   ▼
 ┌───────────────────────── jprod (Docker Compose, one subdomain, HTTPS) ─────────────────────────┐
 │  Pretix  (events, registrations, QR ticket PDFs, check-in lists, teams, webhooks, exports)     │
 │  Postgres 16  (pretix db + n8n db)      Redis 7      Caddy (TLS)  [profile: only if no proxy]  │
 │  n8n  (self-hosted community edition: forms, schedules, WhatsApp, Data Tables, message log)    │
 └───────────────────┬───────────────────────────────────────────────┬───────────────────────────┘
                     │ REST + webhooks                               │ Cloud API + status webhooks
                     ▼                                               ▼
              Google Drive (brochure PDFs)                 Meta WhatsApp Business Platform
              Google Sheets (per-broker reports)           (own WABA, INR billing, approved templates)
```

### Component → requirement map (all capabilities verified against official docs; URLs in appendix)

| Requirement (from the capabilities doc) | Component | Verified capability |
|---|---|---|
| 1.1/1.2 Create, edit, delete, search events; date/time/location | **Pretix** event series (`has_subevents`) — each real event is a *subevent* with `name`, `date_from`, `date_to`, `location`, `meta_data` | Subevent resource + `POST .../subevents/`; admin UI does CRUD with no code |
| 1.1 Attach 1–2 page PDF | Google Drive file (anyone-with-link) + its file id stored in the subevent meta property `brochure` | Subevent `meta_data`; template URL button with dynamic suffix |
| 2.2 Broker registers client (name, +91 phone) | **n8n Form** (Phase 1) → `POST /api/v1/organizers/{org}/events/{ev}/orders/` | Order create accepts `positions[].attendee_name`, `positions[].subevent`, `positions[].answers[]`; `email` optional; `send_email` defaults false |
| 2.2 Phone validation | n8n regex + Pretix question type `TEL` | Question types verified: `TEL – telephone number` |
| 2.3 Registration persisted, timestamped, status | Pretix order (status paid/canceled) + position `checkins[]`; n8n Data Table `registration_index` | Orders resource |
| 3.1 Confirmation with QR | Pretix ticket PDF (opaque 32-char secret in the QR, no PII) fetched via `GET .../orderpositions/{id}/download/pdf/`, sent as a WhatsApp **utility template with DOCUMENT header** | Pretix ticket secrets doc; Meta template headers Image/Document; Meta media upload |
| 3.1 24h and day-of reminders | n8n Schedule Trigger + Pretix subevents/orderpositions + utility templates | n8n Schedule Trigger (cron); Meta template rules |
| 3.1 Post-event thank-you / missed-you | n8n Schedule Trigger + `checkins[]` split + templates | Pretix orderpositions carry check-ins; Meta categories |
| 3.2 Templates, delivery status, retry, audit log | Meta WhatsApp Manager (templates), n8n **WhatsApp Trigger** (`messages` + status filter sent/delivered/read/failed), Data Table `message_events` (insert-only) | n8n node definitions read from the instance |
| 4.1 Digital check-in, search, one-tap, visual confirmation | **pretixSCAN** (Android, offline mode) on check-in lists; staff devices with `event.orders:checkin` only | pretixSCAN docs; teams permission list |
| 4.1 "QR from the SMS" | Yes, but the QR carries an opaque ticket secret, never name/phone/broker (see pushbacks) | Ticket secrets doc |
| 4.2 Live attendance dashboard | Pretix check-in list status (`GET .../checkinlists/{id}/status/` and backend page) + pretixSCAN stats | Check-in lists API |
| 4.3 Check-in timestamp + staff/device | Pretix check-in records (list, datetime, device) | Check-in API |
| 5.x Segmentation, per-broker metrics | `broker_code` question answer + `checkins[]` → n8n Summarize → Google Sheet; Pretix exports for everything else | Orders/answers exports |
| 6.x Storage, relationships, audit | Pretix (orders ⇄ positions ⇄ check-ins ⇄ answers, order log) + n8n executions + `message_events` | — |
| 7.1 Admin functions | Pretix backend (Admin team) + two admin n8n forms (broker import, manual send) | Teams API |
| 7.2 Broker functions | Signed link (Phase 1) → WhatsApp Flow (Phase 4) | n8n Form Trigger hidden-field query param; WhatsApp Flows verified |
| 7.3 Staff functions | pretixSCAN; optional n8n "find by phone" form | — |
| 8.x Backend/API/queue/scheduler | Pretix REST + webhooks; n8n as queue/scheduler | — |

### Why Pretix, and why not the runners-up

- **Pretix** is the only candidate that satisfies the mechanical chain end-to-end: programmatic orders
  with custom answers, ticket PDF with QR via API, ~45 named webhooks (incl. `pretix.event.order.placed`,
  `pretix.event.checkin`), an offline scanner app, event-scoped teams, free self-hosting.
- **Hi.Events** (runner-up) has native affiliate codes (= broker attribution) and account-free web
  check-in, but **no API route returns a ticket PDF or QR** (email-only), no phone field, online-only
  scanning, and a flat 3-role model. It would need two patches to an AGPL codebase. Rejected.
- **Budibase + n8n custom portal** would work (Budibase has a free Barcode/QR scanner component and
  row-level filtering) but it is building the app in low-code, which the user explicitly does not want.
  Kept as the fallback if Pretix's licence (below) is a problem.
- Indico has no documented write API; alf.io has no webhooks; Eventyay is undocumented.

### Two things about Pretix the user must accept or reject

1. **Licence.** AGPLv3 plus additional §7 terms: no SaaS to third parties, keep the "powered by pretix"
   footer (may be rephrased), and *"Using pretix to organize, promote, or sell products or services
   offered or executed by third parties"* is prohibited. JSL running its own events through its own
   broker network is, in my reading, JSL's own service, so this should be fine. **I am not certain; a
   lawyer should read https://github.com/pretix/pretix/blob/master/LICENSE before go-live.** Fallback:
   Budibase-based portal (Phase 1 form and all n8n workflows carry over unchanged).
2. **No "broker sees only own registrations" permission exists in Pretix** at any price. That is fine
   here because brokers never touch the Pretix backend: their surface is the n8n form (Phase 1) and
   WhatsApp (Phase 4). Attribution lives in a hidden `broker_code` question. The paid "Resellers"
   plugin is not needed.

### Why self-host n8n on jprod instead of the connected n8n Cloud instance

The connected instance is `cornerstoneschool.app.n8n.cloud`, another project's workspace, and n8n Cloud
bills per execution: one 500-person event produces roughly 2,000–6,000 trigger executions from WhatsApp
status webhooks alone (approximate). A self-hosted community-edition n8n on jprod is free, has unlimited
executions, reaches Pretix and Postgres on the private Docker network, keeps client data on JSL's server,
allows `crypto` in the Code node, and supports Data Tables. The Cloud instance remains a convenient
build/test sandbox (this session can author workflows there via MCP and export the JSON into the repo).

## Data model

**In Pretix (system of record for events, registrations, attendance):**
- Organizer `jsl` → Event `investor-events` (series, `has_subevents: true`, timezone Asia/Kolkata,
  test mode OFF because pretixSCAN rejects test-mode tickets) → one subevent per real event.
- Organizer meta property `brochure` (Google Drive file id) set per subevent.
- Item `Registration` (price 0, admission ticket, personalised, attendee name required).
- Questions on the series: `client_phone` (`TEL`, required), `broker_code` (`S`, hidden),
  `consent_given` (`B`, hidden), `consent_version` (`S`, hidden).
- One order = one registration = one position. `attendee_name` = client name. Ticket secret = QR.
  Order `comment` = "via broker BRK123 (link)" for humans.
- One check-in list per subevent ("Main entrance", all products). Attended = has a check-in on that
  list. No-show = paid order, no check-in, event ended. Cancelled = order cancelled.
- Teams: `Admins` (all permissions), `Door staff` (only `event.orders:checkin` + `event.orders:read`,
  devices for pretixSCAN), `Automation` (API token: `event.orders:read|write`, `event.subevents:read`,
  `event.items:read`, `event.vouchers:read`, `organizer.webhooks:write`).

**In n8n Data Tables (small, operational):**
- `brokers`: code, name, phone_e164, active, created_at, source (csv|admin).
- `registration_index`: phone_e164, subevent_id, order_code, broker_code, created_at. Used for duplicate
  checks and phone lookup (Pretix's search does not cover question answers).
- `message_events` (insert-only, so no read-modify-write race): wamid, order_code, phone_e164,
  template, category, status (accepted|sent|delivered|read|failed), error_code, ts.
- `optouts`: phone_e164, reason (131050|STOP|manual), ts.

## The glue: n8n workflows (all exported as JSON into the repo)

| # | Workflow | Trigger | Nodes (all native; no npm packages) |
|---|---|---|---|
| W1 | `broker-registration-form` | Form Trigger, public URL `/form/register`, hidden fields `broker` + `sig` filled from query string | Code (5 lines: HMAC-SHA256 of broker code with `crypto`, compare to `sig`) → Data Table get `brokers` → HTTP Request `GET .../subevents/?active=true` → Form page (`defineForm: json`, event dropdown built by expression, client name, phone, consent checkbox with the notice text as an HTML field) → Set (normalise phone to `+91XXXXXXXXXX`, regex `^[6-9]\d{9}$`) → Data Table `rowNotExists` on `registration_index` → HTTP Request `POST .../orders/` (status `p`, `payment_provider: free`, position with subevent + answers) → Data Table insert `registration_index` → Form completion ("Registered. Ref {code}. The client will get a WhatsApp pass." Phase 1 interim: `returnBinary` with the ticket PDF so the broker can forward it manually until WhatsApp is live) |
| W2 | `pretix-order-placed` | Webhook node (Pretix webhook action `pretix.event.order.placed`; payload is thin, so re-fetch) | HTTP Request `GET .../orders/{code}/` → Wait/retry loop on 409 for `GET .../orderpositions/{id}/download/pdf/` (Response Format: File) → WhatsApp node `media.mediaUpload` (binary) → HTTP Request `POST https://graph.facebook.com/<ver>/<PHONE_NUMBER_ID>/messages` (template `reg_confirm_v1`, DOCUMENT header by media id, body params, URL button suffix = brochure file id) → Data Table insert `message_events` (wamid, `accepted`) |
| W3 | `reminders` | Schedule Trigger hourly | HTTP Request subevents → Filter `date_from` in [now+24h, now+25h) (and a second branch [now+3h, now+4h)) → HTTP Request `GET .../orderpositions/?subevent=&order__status=p` → Data Table `rowNotExists` on `message_events` (order_code + template) → skip `optouts` → HTTP Request send template `reminder_24h_v1` / `reminder_dayof_v1` → insert `message_events` |
| W4 | `post-event-followup` | Schedule Trigger daily 10:00 IST | Subevents that ended yesterday → orderpositions → If `checkins.length > 0` → `thanks_feedback_v1` else `missed_you_v1` (skip `optouts`) → insert `message_events` |
| W5 | `whatsapp-inbound-and-status` | WhatsApp Trigger (`messages`, status filter: sent, delivered, read, failed) | If `statuses[]` → insert `message_events` (error 131050 → also insert `optouts`); if inbound text `STOP` → `optouts`; quick-reply "Can't attend" → HTTP Request Pretix cancel order (endpoint confirmed at build time); Phase 4: route Flow submissions (`nfm_reply`) to registration |
| W6 | `broker-directory-admin` | Form Trigger (basic auth, admins) with two modes: CSV upload or single broker (name + phone) | Extract from File (csv) → Set (E.164, generate code `BRK` + 4 digits if absent) → Data Table upsert `brokers` → Code (HMAC) → Set `link` → optional WhatsApp `broker_link_v1` send → completion page listing links |
| W7 | `per-broker-report` | Schedule (day after event) or admin form | orderpositions with answers + checkins → Summarize (group by `broker_code`: registered, attended, no-show) → Google Sheets append (spreadsheet "JSL Event Reports", one tab per event) |
| W8 | `admin-manual-send` (optional) | Form Trigger (basic auth) | pick subevent + template + filter (all / attended / no-show) → send → log |
| W9 | `staff-find-by-phone` (optional) | Form Trigger (basic auth, door staff) | Data Table get `registration_index` by phone → shows order code + name for pretixSCAN search |

Only W1's HMAC check and W6's link generation contain code, about five lines each. Everything else is
native nodes and expressions.

## WhatsApp templates (versioned, never edited in place)

| Name | Category | Header | Body variables | Buttons |
|---|---|---|---|---|
| `reg_confirm_v1` | UTILITY | DOCUMENT (ticket PDF) | name, event, date/time, venue, ref | URL "Event brochure" (dynamic suffix = Drive file id); QUICK_REPLY "Can't attend" |
| `reminder_24h_v1` | UTILITY | none | name, event, time, venue | URL "Venue map" (dynamic) |
| `reminder_dayof_v1` | UTILITY | none | name, event, time, venue | same |
| `thanks_feedback_v1` | UTILITY (framed as feedback survey tied to the event; a plain thank-you is MARKETING) | none | name, event | URL "Share feedback" |
| `missed_you_v1` | MARKETING (retargeting; ~7–9× utility price, user-suppressible) | none | name, event, next-step | URL "Recording / next event" |
| `broker_link_v1` | UTILITY | none | broker name, link suffix | URL (dynamic suffix = signed link) |
| `broker_register_flow_v1` (Phase 4) | UTILITY | none | broker name | FLOW button |

Hard rules learned from Meta's docs: every one of these is business-initiated, so all are templates
(no free-form messages); review takes up to 24h; approved templates can be edited only rarely (reported
1×/24h, 10×/30d, not verified from Meta's own page), so a copy change is a new `_v2`; Meta may
re-categorise utility→marketing silently if wording is promotional, which changes the price.

## Broker identity

- **Phase 1 (web form):** each broker gets `https://n8n.<domain>/form/register?broker=BRK123&sig=<hmac>`.
  Anyone with the link can register under that code (accepted risk; low impact). Links are sent via
  WhatsApp (`broker_link_v1`) once Meta is live, manually before that.
- **Phase 4 (inside WhatsApp):** broker messages the JSL number → W5 matches the sender to `brokers`
  → replies with an interactive Flow message (`interactive.type: "flow"`, `flow_action: "navigate"`,
  `flow_action_payload.data` = the list of upcoming events, no endpoint needed) → screen: Dropdown
  (events, ≤20), TextInput `phone`, TextInput name, OptIn consent → submission arrives as
  `interactive.type: "nfm_reply"` with `response_json` and the `flow_token` we set → same order-create
  path as W1. Flow JSON lives in `whatsapp/flows/` and is published via the Flows API. All of this is
  verified in Meta's Flows docs; only feature availability for a brand-new Indian WABA is unverified.

## Repository layout (files this plan creates)

```
README.md                                  what this is, architecture picture, where to start
docs/architecture.md                       this design, component map, data model
docs/decisions/ADR-001-pretix.md           incl. licence caveat and Hi.Events comparison
docs/decisions/ADR-002-whatsapp-cloud-api.md  incl. why not Vapi/BSP/unofficial gateways
docs/decisions/ADR-003-broker-identity.md  signed links now, WhatsApp Flow later
docs/runbooks/01-jprod-preflight.md        run scripts/preflight.sh, paste output, pick compose profile
docs/runbooks/02-deploy-stack.md           DNS, .env, compose up, pretix cron, backups, upgrades
docs/runbooks/03-meta-whatsapp-onboarding.md  business verification, number, display name, INR billing,
                                           system-user token, app for the trigger, template submission,
                                           messaging-limit check (250 → 2,000 needs verification)
docs/runbooks/04-pretix-setup.md           organizer, series event, item, questions, check-in list,
                                           teams, devices for pretixSCAN, webhook to n8n, meta property
docs/runbooks/05-n8n-setup.md              import workflows, credentials, Data Tables, activate
docs/runbooks/06-event-day.md              tablets, offline mode, phone lookup, live counts
docs/runbooks/07-compliance.md             DPDP notice/consent text v1, opt-out, retention, Meta opt-in
deploy/docker-compose.yml                  pretix/standalone:stable, postgres:16, redis:7, n8n, caddy (profile "edge")
deploy/.env.example                        domain, DB passwords, n8n encryption key, SMTP, timezone
deploy/pretix/pretix.cfg.example           [pretix] url/instance_name, [database], [redis], [celery], [mail]
deploy/caddy/Caddyfile.example             events.<domain> → pretix:80, n8n.<domain> → n8n:5678
deploy/reverse-proxy/nginx-snippet.conf    for the case where jprod already runs nginx
scripts/preflight.sh                       read-only: OS, RAM, disk, docker, ports 80/443, containers, public IP
scripts/backup.sh                          pg_dump both DBs + pretix data volume, rotate
n8n/workflows/W1..W9 *.json                importable workflow exports
n8n/data-tables.md                         table schemas to create (brokers, registration_index, message_events, optouts)
whatsapp/templates/*.json                  Business Management API payloads for each template
whatsapp/flows/broker_register.flow.json   Phase 4 Flow JSON
```

## Phased delivery

**Phase 0 — Repo scaffold (this branch, first PR).** Everything in the layout above except the Flow
JSON, with workflow JSON authored/validated in the sandbox n8n and exported. Acceptance: `docker compose
config` validates; `bash -n scripts/*.sh`; workflow JSON passes n8n validation; docs reviewed by the user.

**Phase 1 — Registrations without WhatsApp (works before Meta approval).** User runs the pre-flight,
deploys the stack on jprod, follows runbook 04, imports W1/W6/W7/W9. Brokers get links; the form returns
the ticket PDF for manual forwarding. Acceptance: a broker link creates a paid order in Pretix with the
four answers; duplicate phone for the same event is rejected; pretixSCAN on an Android device checks
the ticket in and the backend status page shows 1/1.

**Phase 2 — WhatsApp automation.** Runbook 03 (start it on day one; it is the long pole). Templates
approved, W2/W3/W4/W5 activated. Acceptance: a test registration produces `reg_confirm_v1` with the PDF
within 60 s; `message_events` shows accepted → sent → delivered; a test event dated +25h receives the
24h reminder at the right hour; a test event ended yesterday sends thank-you to the checked-in ticket
and missed-you to the other; error 131050 lands in `optouts`.

**Phase 3 — First real event.** Under the 250-unique-recipients/24h cap unless business verification is
done. Door staff on two Android tablets, offline mode on, W9 for phone lookup. Post-event W7 sheet.

**Phase 4 — Brokers register inside WhatsApp.** Flow JSON published; W5 extended; `broker_register_flow_v1`
sent to all active brokers; web form kept as fallback.

**Later, only if asked:** Chatwoot as a human inbox (note: Meta allows one webhook URL per app, so n8n
must own it and forward), Vapi voice-call reminders, SMS fallback (needs TRAI DLT registration).

## Verification (end-to-end, per phase)

- Stack: `docker compose -f deploy/docker-compose.yml config`; after deploy, `curl -I https://events.<domain>/control/`
  returns 200/302; `docker exec pretix pretix cron` runs clean; Postgres backups restore into a scratch DB.
- Pretix API: `curl -H "Authorization: Token …" .../subevents/` lists dates; a scripted order create
  (from runbook 04) returns 201 with the answers echoed; `download/pdf/` returns a PDF whose QR
  pretixSCAN accepts.
- n8n: each workflow run with `test_workflow`/manual execution against the Test Event; W1 rejects a
  bad `sig`, a non-Indian number, and a duplicate; W3 is idempotent across two consecutive runs
  (second run sends nothing).
- WhatsApp: Meta's test number to the user's own phone first (Meta allows a small set of pre-verified
  test recipients; exact count to confirm in Meta's docs), then the real number to internal staff; check
  the `statuses[]` webhook rows and that the document opens on iOS and Android WhatsApp.
- Event day rehearsal: 20 internal registrations, two tablets, Wi-Fi off (offline mode), all 20 scanned,
  status page and pretixSCAN stats agree.

## Things in the original document I am pushing back on

1. **QR "generated for the name and phone number of the client along with the broker code."** No. A QR
   holding PII is copyable and forwardable. Pretix's QR is an opaque 32-character secret; scanning it
   resolves to name/phone/broker server-side. Same outcome, no leak.
2. **"Customizable confirmation message" edited by admins.** Not how WhatsApp works. Business-initiated
   messages are Meta-approved templates; admins customise variables and, for copy changes, submit a new
   template version with up to 24h review. Design accordingly (versioned templates, submitted weeks ahead).
3. **"Mark client as No Show" button.** Redundant. No-show is derived: paid, not checked in, event over.
   Explicit cancellation exists (order cancel). Removing the button removes a class of data-entry errors.
4. **"SMS is sent on registration."** WhatsApp is the channel. SMS in India additionally needs TRAI DLT
   registration (entity, headers, templates); out of scope unless asked.
5. **"Track read status."** Best-effort only: a recipient who disabled read receipts never produces `read`.
6. **"Post-event within 24 hours."** Send the next morning at 10:00 IST, not at 23:30 after the event.
   Also budget for the fact that "we missed you" is a MARKETING message (approx. ₹0.86–1.09 vs
   ₹0.12–0.15 for utility, plus 18% GST, numbers from BSP price pages, not verified from Meta's rate card).
7. **"Broker login."** Replaced by signed links now and WhatsApp identity later. A real login would mean
   either giving brokers n8n/Pretix accounts (wrong) or building a portal (what the user does not want).
8. **"Bunch of existing repos."** True for the building blocks, false for the whole. See verdict above.

## Open items the user must own (not software)

- Meta: Business Portfolio, business verification (gates the 250 → 2,000 unique-recipients tier), a phone
  number not already on the WhatsApp app, display name approval, INR billing (mandatory for Indian WABAs
  by 31 Dec 2026 per Meta's pricing updates page), system-user token.
- Legal: Pretix §7 licence read; DPDP Act 2023 consent notice text (v1 drafted in runbook 07 for review;
  substantive obligations reported to bite 13 May 2027, section text not read from an official .gov.in
  source because those sites returned 403); opt-out path.
- Ops: a subdomain (placeholder `events.jslwealth.in` / `n8n.jslwealth.in`, to be replaced), DNS, SMTP
  credentials for Pretix admin mail, two Android devices for the door, a Google Drive folder for brochures.
- jprod facts from the pre-flight (Docker present? reverse proxy present? RAM? ports?).

## Appendix: verified sources (read this session or by the research agents)

- Pretix: orders create/fields https://docs.pretix.eu/dev/api/resources/orders.html · questions/`TEL`
  https://docs.pretix.eu/dev/api/resources/questions.html · subevents https://docs.pretix.eu/dev/api/resources/subevents.html ·
  check-in https://docs.pretix.eu/dev/api/resources/checkin.html · check-in lists https://docs.pretix.eu/dev/api/resources/checkinlists.html ·
  webhooks https://docs.pretix.eu/dev/api/resources/webhooks.html and https://docs.pretix.eu/dev/api/webhooks.html ·
  teams https://docs.pretix.eu/dev/api/resources/teams.html · ticket secrets https://docs.pretix.eu/guides/ticket-secrets/ ·
  pretixSCAN https://docs.pretix.eu/guides/pretixscan/android/ · Docker https://docs.pretix.eu/self-hosting/installation/docker_smallscale/ ·
  licence https://github.com/pretix/pretix/blob/master/LICENSE · hosted pricing https://pretix.eu/about/en/pricing
- Hi.Events: https://github.com/HiEventsDev/Hi.Events (routes/api.php, CreateOrderRequest) · https://hi.events/docs/help-center/check-in/setting-up-check-in ·
  https://hi.events/docs/help-center/marketing-and-promotions/affiliates · https://hi.events/pricing
- Meta WhatsApp: send messages / 24h window https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages ·
  template categories https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-categorization ·
  messaging limits https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits ·
  opt-in https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in ·
  pricing https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing ·
  media https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/media ·
  webhooks https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview ·
  error codes https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes ·
  Flows components https://developers.facebook.com/docs/whatsapp/flows/reference/components ·
  Flow JSON https://developers.facebook.com/docs/whatsapp/flows/reference/flowjson/ ·
  sending a Flow https://developers.facebook.com/documentation/business-messaging/whatsapp/flows/guides/sendingaflow ·
  receiving a Flow response https://developers.facebook.com/docs/whatsapp/flows/guides/receiveflowresponse/ ·
  business terms https://www.whatsapp.com/legal/business-terms
- n8n: node definitions read from `cornerstoneschool.app.n8n.cloud` (WhatsApp v1.1, WhatsApp Trigger v1,
  Form Trigger v2.6, Form v2.5, Data Table v1.1, Postgres v2.7, Extract from File v1.1, Summarize, Google Sheets v4.7) ·
  Form Trigger https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.formtrigger/ ·
  Form https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.form/ ·
  Data tables https://docs.n8n.io/build/work-with-data/data-tables · Code node modules
  https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/configuration-examples/enable-modules-in-code-node ·
  editions https://docs.n8n.io/deploy/host-n8n/community-edition-features · pricing https://n8n.io/pricing/
- Vapi: https://docs.vapi.ai/ · https://vapi.ai/blog/vapi-sms-launch-twilio
- Budibase scanner (fallback path): https://docs.budibase.com/docs/barcodeqr-field
