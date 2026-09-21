# JSL Wealth — Event Management Portal

**Status: PAUSED. Do not build on this yet.**

Development is on hold until the GStack operating system is loaded into the working session and
this repository is restructured to follow its milestone and gate model. Work proceeds strictly
sequentially: one milestone at a time, each verified and closed before the next opens. No parallel
development across milestones.

## Why it is paused

The session that produced the files below could only attach repositories owned by `jhaverios`.
GStack, Graphify, ponytail and headroom could not be read, so the milestone structure they define
has not been applied here. Resolving that needs a session started with the GStack repository as its
initial source.

## What is in the repository right now

Research output and an unvalidated first-pass deployment stack. Neither has been reviewed against
GStack. Treat both as input to the first milestone, not as accepted work.

| Path | What it is | Confidence |
|---|---|---|
| `docs/DRAFT-research-and-architecture.md` | Component research and proposed architecture. Every external claim carries the source URL it was read from; unverified items are marked. | Research verified, architecture unreviewed |
| `deploy/docker-compose.yml` | Pretix + Postgres + Redis + n8n + optional Caddy on one host. | **Never executed.** Docker is unavailable in the authoring environment, so `docker compose config` has not run against it. |
| `deploy/postgres/init-databases.sh` | Creates the `pretix` and `n8n` roles and databases on first boot. | Syntax checked with `bash -n` only |

Empty directories referenced by the draft architecture (`docs/runbooks`, `docs/decisions`,
`scripts`, `n8n/workflows`, `whatsapp/templates`, `whatsapp/flows`) are not yet populated.

## Summary of the research conclusion

There is no production-grade open-source project that combines event registration, QR check-in and
WhatsApp automation. Mature building blocks exist for each part separately. The proposed composition
is Pretix for events, registrations, ticket QRs and check-in, n8n for scheduling and messaging glue,
and Meta's WhatsApp Cloud API for delivery. Reasoning and the rejected alternatives are in the draft
architecture document.

Two items need a decision from someone other than an engineer before any of this ships: the Pretix
AGPL section 7 licence terms as they apply to a broker network, and client consent capture under the
DPDP Act 2023. Both are flagged in the draft.
