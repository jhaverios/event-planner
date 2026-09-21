# Credentials checklist: WhatsApp and email

Everything the integrations need, where each value comes from, and who can get it.
Work top to bottom; later rows depend on earlier ones.

**Never send a value marked SECRET in chat or email.** Put those straight into the
target system: n8n's credential store or the server's `pretix.cfg`. Non-secret IDs
are safe to paste anywhere.

---

## Part 1 — WhatsApp (Meta WhatsApp Business Platform)

### 1a. Prerequisites, in order

| # | Step | Where | Notes |
|---|---|---|---|
| 1 | Meta Business Portfolio exists | business.facebook.com | One per company. JSL may already have one for Facebook or Instagram ads. |
| 2 | **Business verification submitted and approved** | Business settings, Security centre | **The long pole.** Needs certificate of incorporation, proof of address, and a phone or domain check. Until approved you can message only 250 unique people per 24 hours. A 500-guest event fails halfway. |
| 3 | A phone number that is free | n/a | Must **not** be signed into the WhatsApp or WhatsApp Business phone app. If it is, delete that account first and wait. A landline works if it can receive a voice call. |
| 4 | Number added and verified | Meta app dashboard, WhatsApp, API Setup | Verified by SMS or voice code. |
| 5 | Display name approved | WhatsApp Manager, Phone numbers | This is the name clients see. Must relate to the real business. Review takes up to 24 hours. |
| 6 | INR billing attached | WhatsApp Manager, Billing | Meta requires Indian accounts to bill in INR. |

### 1b. Values to collect

| Value | Looks like | Where to find it | Secret? |
|---|---|---|---|
| Business Portfolio ID | 15-16 digits | Business settings, Business info | No |
| WhatsApp Business Account ID | 15-16 digits | App dashboard, WhatsApp, API Setup | No |
| **Phone Number ID** | 15-16 digits | Same page. This is an ID, **not** the phone number. Every send call uses it. | No |
| Display phone number | `+91XXXXXXXXXX` | The number itself | No |
| App ID | ~15 digits | App dashboard, Settings, Basic | No |
| App Secret | 32 hex characters | Same page, click Show | **SECRET** |
| System user token | ~200 characters, starts `EAA` | Business settings, Users, System users, Add, then Generate token | **SECRET** |
| Graph API version | e.g. `v23.0` | Whatever is current when we build | No |

**Generating the system user token.** Create a system user with the Admin role, assign it
the WhatsApp Business Account as an asset, then generate a token with exactly two
permissions: `whatsapp_business_messaging` and `whatsapp_business_management`. Choose a
token that does not expire. A user access token from the Getting Started page expires in
24 hours and is useless for production.

### 1c. Webhook

n8n's WhatsApp Trigger registers the Meta webhook itself and verifies Meta's challenge
against the node's own generated id. There is **no verify token to invent**. If Meta's
console asks for one by hand, it must be that node's id.

One constraint to plan around: **Meta allows one callback URL per app.** n8n will own it.
If anything else needs the events later, n8n forwards them.

### 1d. Message templates

Every message we send is business-initiated, so all of them must be pre-approved
templates. Review takes up to 24 hours each, and approved templates can be edited only
rarely, so treat them as versioned releases rather than editable copy.

| Template | Category | Carries |
|---|---|---|
| `reg_confirm_v1` | Utility | The ticket PDF with the QR code |
| `reminder_24h_v1` | Utility | Event name, time, venue |
| `reminder_dayof_v1` | Utility | Final reminder |
| `thanks_feedback_v1` | Utility | Framed as a feedback survey tied to the event |
| `missed_you_v1` | **Marketing** | Re-engagement. Roughly 7 to 9 times the price of a utility message, and clients can switch it off. |
| `broker_link_v1` | Utility | The broker's personal registration link |

Keep the utility ones strictly factual. Any persuasive wording and Meta silently
reclassifies them as marketing, which multiplies the cost per event.

---

## Part 2 — Email (ZeptoMail SMTP for pretix)

JSL already runs ZeptoMail, Zoho's transactional email service, so pretix uses that rather
than a new provider. In this design pretix emails **administrators only**; clients receive
WhatsApp. Volume is a handful of messages a day.

| Value | Setting | Secret? |
|---|---|---|
| Host | `smtp.zeptomail.in` | No |
| Port | `587` with TLS, or `465` with SSL | No |
| Username | the literal string `emailapikey` | No |
| Password | the send-mail token from the ZeptoMail console | **SECRET** |
| From address | e.g. `events@jslwealth.in`, must be a verified sender | No |

Source: https://www.zoho.com/zeptomail/help/smtp-home.html

### The trap to avoid

With most providers the SMTP username and the From address are the same string. With
ZeptoMail they are not: the username is always `emailapikey` and the From address is a
separate verified sender. Conflating them makes pretix authenticate fine and then send as
the wrong sender, or fail silently. `scripts/deploy-jprod.sh` prompts for the two
separately for this reason.

### Region note, worth checking before you deploy

Zoho runs regional data centres and JSL is likely on the India one. Zoho's public SMTP
page documents only `smtp.zeptomail.com`; I could not confirm a separate India host from
official documentation. **Check the ZeptoMail console under Setup Info, SMTP** and use
whatever host it shows there. If it differs, pass it to the deploy script when prompted.

### What to collect

1. The send-mail token. ZeptoMail console, Mail Agents, pick the agent, SMTP and API, then
   copy or generate a send-mail token. Treat it as a password.
2. Confirmation that the From address you want is a verified sender on the agent. If
   `events@jslwealth.in` is not set up yet, add and verify it first, or mail will be
   rejected at send time rather than at configuration time.
3. The exact host string from Setup Info.

Nothing here needs DNS work if the domain is already verified in ZeptoMail for the other
Jhaveri systems, which is the likely case.

## Part 3 — Where each value goes

| Value | Destination | Set by |
|---|---|---|
| SMTP host, port, user, password, from | `deploy/pretix/pretix.cfg` on the server | `scripts/deploy-jprod.sh` prompts for these on first run |
| Phone Number ID, system user token | n8n credential "WhatsApp Business Cloud" | Typed into n8n's UI, never into a file |
| App ID, App Secret | n8n credential "WhatsApp Trigger" | Same |
| Business Portfolio ID, WABA ID | `docs/` reference only | Not secret |

Nothing in this list belongs in the repository. `.env` and `pretix.cfg` are gitignored and
written with mode 600 by the deploy script.

---

## Part 4 — Realistic timeline

| Item | Typical wait |
|---|---|
| App password for SMTP | Minutes |
| Phone number verification | Minutes |
| Display name approval | Up to 24 hours |
| Each message template | Up to 24 hours |
| **Business verification** | **Days to weeks** |

Business verification gates everything above 250 recipients per day, so start it first and
in parallel with the build. Nothing else on this list is on the critical path.


## ZeptoMail: settled, with proof

Read from the JSL agent's SMTP page and verified against the API.

| Field | Value |
|---|---|
| Host | `smtp.zeptomail.in` |
| Port | 587 TLS, or 465 SSL |
| Username | `emailapikey` |
| Sender domain | `jslwealth.in`, verified |
| From | `events@jslwealth.in` |

**The region is proven, not assumed.** The same token returns a *validation* error on
`api.zeptomail.in`, meaning it authenticated, and **401 Invalid API Token** on `api.zeptomail.com`.
Following Zoho's public docs, which list only the global host, would have produced an error that
reads like a wrong password rather than a wrong region.

**SMTP could not be tested from the build environment.** Outbound port 587 is blocked there, as is
22. The credential was proven over HTTPS instead. Sending an actual email has to be checked on jprod,
and runbook 02 should not be signed off until it has been.

**The trap that cost an hour: file permissions.** pretix runs as uid 15371 inside its container. A
root-owned `pretix.cfg` at mode 600 is unreadable to it, and pretix does not complain. It silently
falls back to built-in defaults, so `EMAIL_HOST` quietly becomes `localhost:25` and mail never
leaves. Confirm after any config change:

```bash
docker compose exec -T pretix python3 -c \
  "from django.conf import settings; print(settings.EMAIL_HOST, settings.EMAIL_HOST_USER)"
```

If that prints `localhost` the file is not being read. Fix with
`chown 15371 pretix.cfg && chmod 600 pretix.cfg`, then recreate the container, because a changed
bind-mounted file does not restart it on its own.
