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

## Part 2 — Email (SMTP for pretix)

In this design pretix emails **administrators only**. Clients receive WhatsApp. So the
volume is a handful of messages a day and a transactional provider is unnecessary.

| Value | Example | Secret? |
|---|---|---|
| Host | `smtp.gmail.com` | No |
| Port | `587` | No |
| Encryption | STARTTLS on, SSL off | No |
| Username | `events@jslwealth.in` | No |
| Password | 16-character app password | **SECRET** |
| From address | `events@jslwealth.in` | No |

### How to get it, Google Workspace

1. Create or choose a dedicated mailbox, for example `events@jslwealth.in`. Do not use a
   personal account; when that person leaves, the system breaks.
2. Turn on two-step verification for that mailbox. Google will not issue an app password
   without it.
3. Go to `myaccount.google.com/apppasswords`, create one named "event portal", and copy
   the 16 characters.
4. The app password is scoped to mail only and revocable in one click, which makes it far
   lower risk than a cloud key.

Limits are roughly 2,000 messages per day on Workspace, which is far above what admin
mail needs.

### If you would rather not use Workspace

Amazon SES or Brevo both work and give better deliverability, but both need SPF and DKIM
records added to `jslwealth.in` DNS. That is more setup than admin mail justifies. Revisit
only if pretix ever emails clients directly.

---

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
