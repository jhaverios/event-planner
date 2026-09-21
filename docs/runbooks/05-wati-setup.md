# Runbook 05 — WATI setup

Read from the live account on 2026-09-21, not from documentation.

## Connection

| Value | Setting |
|---|---|
| Base URL | `https://live-mt-server.wati.io/111557` |
| Tenant | `111557` |
| Channel number | `917041771021` |
| Auth header | `Authorization: Bearer <token>` |
| Token storage | n8n credential store and `deploy/.admin-credentials`, both gitignored |

Verified: `GET /api/v1/getMessageTemplates` returns 200.

**The token was shared over chat, so rotate it before go-live.** Connector, then API, then
regenerate. Also note WATI invalidates the token whenever the account password changes, which
surfaces mid-event as a 401, so the workflows should alarm on 401 rather than fail quietly.

## What is already on the account

67 templates, 23 approved. None of them fits a registration confirmation carrying a per-person QR,
so new templates are needed. The history is still useful.

### Header type codes

The API returns `header.type` as an integer. Observed mapping:

| Code | Meaning | Example on the account |
|---|---|---|
| 1 | text | `im_registration_message` |
| 2 | image | `im2025_checkin3`, approved |
| 4 | document | `onboarding_signoff`, approved and Utility |

Two useful precedents. `onboarding_signoff` is **approved as Utility with a document header**, so a
document header is not itself a barrier to Utility classification. `im2025_checkin3` is **approved
with an image header**, though its media is a fixed asset rather than a per-recipient URL.

### Why the previous registration template was rejected

`im_registration_message` was submitted as **Utility** and **rejected**. Its body:

> *Hello {{name}}*, We're thrilled to have you on board. 🙌 Check out the event agenda by visiting
> our website. 🗓️ To access exclusive resources like speaker PPTs, log in to the website and
> download them. 📥 Get ready for an incredible learning experience! 📈 #InvestmentMasterclass
> #WelcomeParticipants

That is promotional copy in a transactional category. "Thrilled to have you on board", "exclusive
resources", "incredible learning experience" and campaign hashtags are all marketing signals. Meta
classifies on content, not on the category you declare.

**This is the single most useful thing on the account.** Our confirmation template must be
ruthlessly factual: who, what, when, where, reference number, and the QR. No enthusiasm, no
hashtags, no invitations to browse. Anything warmer either gets rejected as Utility or silently
reclassified as Marketing, which costs roughly seven to nine times as much per message and lets
recipients switch it off.

### Variable naming is inconsistent on this account

Bodies use positional `{{1}}`, `{{2}}` while `customParams` names them (`name`, `dashboard_url`).
Both styles exist across the 67 templates. Read each template's actual tokens with
`getMessageTemplates` before wiring a send; do not assume.

## Still outstanding

**The plan tier.** WATI's pricing page states Growth ships with no webhooks, and delivery status is
webhook-only. Until the tier is known, we cannot say whether "was it delivered" is answerable at
all. This is the one open question from step 1.

## First real event

Created in pretix as subevent 2:

| Field | Value |
|---|---|
| Name | Look Beyond the Headlines — Contra Fund & SIF |
| Speaker | Sanket Joshi, Cluster Head Baroda, ICICI Prudential AMC |
| Starts | 2026-09-24 18:30 IST, dinner after |
| Venue | Hotel The Fern, Behind Dinesh Mills, Akota, Vadodara 390020 |
| Capacity | 200 |
| RSVP on the card | Vimal Pandya, 9712989074 |

It already appears in the broker registration form.

**The date is three days out.** Template review takes up to 24 hours each, and the plan tier
question is unresolved. That timeline is the real risk on this event, not the software.

## Verified against the live API, not the docs

**Webhooks really are off on Growth.** `GET /api/ext/v3/webhooks` returns **403** with this account's
token. The pricing page said so; the API agrees. Delivery status therefore comes from polling
`GET /{tenant}/api/v1/whatsApp/messages/{phone}/{localMessageId}`, and step 5 is a scheduled poller
rather than a webhook receiver.

**`local_message_id` must be 10 to 64 characters.** The send endpoint accepts a shorter one and
returns `success: true`, but the status endpoint then refuses to look it up:
`Local message ID length must be between 10 and 64 characters`. A message sent with a 7-character id
is unqueryable forever. Generate ids well over the minimum, for example
`reg-<ordercode>-<touchpoint>-<epoch>`.

**A 200 from the send endpoint means almost nothing.** Sending to `910000000000`, which is not a
valid Indian mobile, returned `success: true` with an empty `errors` array. WATI accepts the request
and validates asynchronously. The ledger must record "accepted" on the response and only move to
sent, delivered or failed from a later status read.

**Still untested: whether an approved template with fixed media accepts a per-recipient image URL.**
Passing an extra header parameter to `im2025_checkin3` was accepted, but so was everything else, so
the 200 proves nothing. This needs one real recipient number to settle.

## The template-reuse question: answered, and the answer is no

Tested on 2026-09-21 by sending `im2025_checkin3` to a real handset with an extra header parameter
pointing at a per-recipient QR image.

**Result: the message was DELIVERED, and it carried the template's baked-in image, not ours.**
The status record shows the header that actually went out:

```
template.header.headerTypeString = image
template.header.mediaHeaderId    = 1489643052186150
template.header.mediaFromPC      = WhatsApp_Image_2025_10_11_at_10.44.13-....jpeg
statusString                     = DELIVERED
```

That media id is the file uploaded when the template was created. Our `header_image` parameter was
accepted without complaint and silently ignored.

**So an approved template whose media was a fixed upload cannot carry a per-recipient QR.** WATI's
documentation was right: the header URL has to be declared as a variable at template creation, by
ticking "Add a different header". Reuse is off the table for the pass; one new template is required
and Meta has to review it.

This is why the test was worth ten minutes. Both the send call and the status poll returned success
throughout, so nothing short of looking at the delivered header would have revealed it.

### What the same test proved that is good news

**Delivery tracking works on the Growth plan.** Polling
`GET /{tenant}/api/v1/whatsApp/messages/{phone}/{localMessageId}` returned `statusString: DELIVERED`
within about 20 seconds, along with `finalText` showing the rendered body with variables substituted.
No webhooks needed. Step 5's scheduled poller is viable exactly as planned.

Useful fields on that record: `statusString`, `eventType`, `finalText`, `template.header.*`, and on
failure `failedCode` and `failedDetail`.
