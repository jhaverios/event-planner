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


## jsl_event_pass, as WATI actually recorded it

Submitted 2026-09-21, status PENDING. Two details decide how the send call must be written.

**The header is a link, not an upload.** `header.link` holds the sample URL and both `mediaHeaderId`
and `mediaFromPC` are empty. That is the same shape as `onboarding_signoff` and the opposite of
`im2025_checkin3`, whose baked-in upload defeated the earlier test. Link-type headers are the ones
WATI is more likely to substitute per message, so this is the promising configuration. Still unproven
until a real send.

**The button's variable collides with the body's.** WATI recorded:

```
buttons[0].parameter.urlType          = dynamic
buttons[0].parameter.buttonParamMapping = {"index": 1, "paramName": "1"}
customParams                          = ['1','2','3','4','5']
```

The button's dynamic suffix is mapped to a parameter named `1`, and the body's first variable, the
client's name, is also named `1`. If WATI resolves them from one flat namespace, sending
`{"name":"1","value":"Rahul Mehta"}` puts the client's name into the pass URL and the pass becomes
unreachable, while the greeting still looks correct. The message would appear fine and the button
would be broken.

Test this deliberately on the first send after approval: send it, then open the button URL from the
received message rather than trusting the send response. If the two collide, the fix is to resubmit
with the body starting at `{{2}}` so the numbering cannot overlap.

## Sending the pass by email

Outbound SMTP is blocked in the build environment, from the shell and from inside the containers
alike, so pretix cannot send mail here. ZeptoMail's HTTPS API is reachable and is what W7 uses:
`POST https://api.zeptomail.in/v1.1/email` with `Authorization: Zoho-enczapikey <token>`.

This is useful beyond the workaround. It means the pass can be delivered by email today, while the
WhatsApp template is still in review, and the two channels share one signed ticket link.

## jsl_event_pass v1: approved, and broken in two ways

Approved 2026-09-21 and immediately tested with a real send. It **FAILED**, and the reason is exact:

```
statusString  = FAILED
failedDetail  = Dynamic URL button parameter cannot contain spaces
header LINK   = https://api.qrserver.com/...&data=SAMPLE123   (the approval sample)
BUTTON url    = https://events.jslwealth.in/webhook/ticket?t={{1}}   (never substituted)
finalText     = Hello Rahul Mehta, your registration is confirmed. ...
```

**Fault one: the button and the body share parameter `1`.** WATI tried to put the client's name into
the dynamic URL and refused it for containing a space. So every client whose name has a space, which
is nearly all of them, fails. The collision predicted from `buttonParamMapping` is real.

It failing loudly is fortunate. Had the name been a single word the message would have sent, and the
button would have pointed at `?t=Rahul` for the rest of the event.

**Fault two: a plain URL in the header is static.** The header link stayed on the approval sample.
Extra `header_image` and `media_url` parameters were accepted and ignored, exactly as on
`im2025_checkin3`. Pasting a URL rather than uploading a file did not make the header dynamic.

### What the body proved

Body variables substitute perfectly: name, event, date, venue and reference all rendered. So **text
is the one channel we can rely on**, and the design should lean on it.

### v2 design

| Part | Decision |
|---|---|
| Body | Six variables. `{{6}}` carries the full pass URL as plain text. WhatsApp auto-links it. |
| Button | **None.** Removing it removes the collision entirely. |
| Header | Image, with the header variable set via the only free-form attribute WATI's UI exposes, `product_image_url` under Shopify. Ignore the commerce label; it is just a named placeholder. |
| Header sample | A neutral JSL-branded image, **not a QR**. If substitution fails, clients see branding rather than a QR that is not theirs. |

The reasoning: a link in the body is certain to work because body substitution is proven. The image
header is an upgrade that either works or degrades to branding. Nothing in the message is ever wrong,
which is not true of v1, where a failed substitution would have shown every client the same sample QR.

A URL in the body also removes the dependency on `events.jslwealth.in` being the exact approved
domain, since the whole URL is a variable rather than a baked-in prefix.

## Creating templates through the API

`POST /{tenant}/api/v1/whatsApp/templates` works, with two traps.

**It returns HTTP 500 on success.** The template is created correctly; the 500 is noise. Always read
the template list back rather than trusting the status code. A second call then fails with
"template with current name already exists", which is the real confirmation that the first worked.

**It only ever creates a DRAFT.** There is no submit or publish endpoint: the documented template
endpoints are get, create, and delete, plus webhooks that report status changes. Sending a draft to
Meta for review must be done by a person clicking **Save and submit** in the dashboard.

Deleting is `DELETE /{tenant}/api/v1/whatsApp/templates/{wabaId}/{name}`, which returns `{"ok":true}`.
The wabaId for this account is on every template in the list response.

A minimal working create payload:

```json
{
  "elementName": "jsl_event_pass_v2",
  "category": "UTILITY",
  "language": "en",
  "body": "Hello {{1}}, ... Your entry pass: {{6}}",
  "footer": "",
  "buttons": [],
  "customParams": [{"paramName": "1", "paramValue": "Rahul Mehta"}, ...]
}
```

WATI fills in `subCategory: STANDARD`, `buttonsType: none` and `type: hsm` by itself.

## Template inventory after this work

| Name | Status | Verdict |
|---|---|---|
| `jsl_event_pass` | APPROVED | **Broken, do not use.** The dynamic URL button shares parameter `1` with the body, so any client whose name contains a space fails to send. |
| `jsl_event_pass_v2` | DRAFT, awaiting submit | The one to use. Six body variables, the sixth carrying the pass URL. No header, no buttons, so nothing can collide and nothing can silently show the wrong image. |

`jsl_event_pass` should be deleted once v2 is approved, so nobody reaches for it later. That is a
deliberate decision to leave to a person, since deleting an approved template is not reversible
without another Meta review.
