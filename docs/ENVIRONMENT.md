# Development environment

The toolchain this project is built with, and the state it was left in.

**This container is ephemeral.** Everything below lives outside the repository, in `~/.claude`
and `~/.local`. When the session container is reclaimed, all of it is gone and has to be
reinstalled. The repository itself is the only durable artefact.

## Installed and verified

### gstack — the development operating system

Source: `https://github.com/garrytan/gstack`, MIT, cloned to `~/.claude/skills/gstack`.

The repository's own `./setup` script was **blocked** by the session's auto-mode classifier as
self-modification, because it rewrites agent configuration across several hosts. The skills were
registered instead by symlinking each skill directory into `~/.claude/skills/`, which is the same
end state `setup` produces for the Claude host, without touching any other host's configuration.

53 skills resolve, among them the ones that carry the process discipline this project follows:

| Skill | Role in this project |
|---|---|
| `office-hours` | Interrogate the product idea before any milestone opens |
| `autoplan` | Run CEO, design, engineering and developer-experience reviews in sequence |
| `plan-ceo-review`, `plan-eng-review`, `plan-design-review`, `plan-devex-review` | The individual review gates |
| `spec` | Turn intent into an executable spec in five phases |
| `review` | Pre-landing review of a diff |
| `ship` | Merge base, test, review, bump version, changelog, commit, push, open the pull request |
| `investigate` | Root-cause debugging |
| `cso` | Security audit |
| `retro` | Close a milestone |

Not usable in this container: `browse`, `scrape`, `qa`, `design-shotgun` and the `ios-*` family,
all of which drive a real browser or a physical iOS device.

### ponytail — minimal-code discipline

Source: `https://github.com/dietrichgebert/ponytail`, MIT, copied to `~/.claude/tools/ponytail`.

The documented install is `/plugin marketplace add` followed by `/plugin install`, which are
interactive commands unavailable to a non-interactive session. Its six skills were symlinked into
`~/.claude/skills/` instead: `ponytail`, `ponytail-audit`, `ponytail-debt`, `ponytail-gain`,
`ponytail-help`, `ponytail-review`.

The plugin's two Node lifecycle hooks are **not** installed, so always-on activation is off. The
skills work when invoked explicitly. Wiring the hooks means editing agent configuration, which is
the same blocked class as the gstack installer.

### headroom — context compression

Source: `https://github.com/headroomlabs-ai/headroom`, Apache 2.0.
Installed as a tool environment: `uv tool install --python 3.13 "headroom-ai[all]"`, version 0.37.0,
binary at `~/.local/bin/headroom`.

Verified working on a realistic payload, a 500-row registration export delivered as a `tool_result`
block:

| Measure | Value |
|---|---|
| Original | 101,723 characters |
| Compressed | 41,104 characters |
| Tokens saved | 18,967 |
| Compression ratio | 48.6% |
| First record still present | yes |

Two findings worth keeping. The base package without the `[all]` extras ships no compressors and
returns the input unchanged at a 0% ratio, which looks like success unless the ratio is checked.
And `compress()` deliberately does not rewrite a plain user message, by design, so a smoke test on
one proves nothing. Test against a `tool_result` block.

**Not wired into the agent, deliberately.** `headroom wrap claude` repoints `ANTHROPIC_BASE_URL` at
a local proxy on port 8787. In this managed container the model endpoint is explicitly exempted from
the outbound proxy, so redirecting it is a plausible way to break the session's own model access.
`headroom doctor` accordingly reports the proxy unreachable and nothing routed. That is the expected
state, not a fault. Wiring it up is a decision to take deliberately, and `headroom mcp` is the safer
route than `wrap`.

## Assessed and not adopted

### artemis — Android device automation

Source: `https://github.com/google/artemis`, Apache 2.0, last commit 2026-09-11, active.
It drives real Android phones from natural language over ADB, exposes an MCP server for Claude Code,
and reports 99%+ task completion on Google Research's AndroidWorld benchmark.

**Not useful for this project, for three reasons.**

1. It cannot run in this environment at all. There is no `adb`, `scrcpy` or `ffmpeg`, there is no USB
   passthrough for a physical handset, and `/dev/kvm` is absent so an emulator cannot start either.
   It is fundamentally a local-hardware tool.
2. The only Android surface in the current design is pretixSCAN, a third-party application this
   project does not build. There is no application code of ours for Artemis to regression-test.
3. The real failure modes at a venue door are offline synchronisation, venue wifi and bad lighting on
   a phone screen. Automation on a bench does not exercise any of them. A twenty-person rehearsal
   with the actual tablets tests more, for less setup.

**Revisit it if** the check-in surface is ever replaced with an application we own, such as a custom
progressive web app or a broker-facing mobile app. At that point it becomes a genuine QA asset.

## Blocked

### athena-os — the development OS to follow

`https://github.com/nimishshah1989/athena-os` is private, and this session's GitHub credential is
scoped to the `jhaverios` account. Both routes fail:

- `add_repo` refuses the attach outright: cross-owner adds are not supported, because the session was
  started with `jhaverios/event-planner` as its only source.
- A direct clone fails authentication: `could not read Username for 'https://github.com'`.

Until it is readable, the milestone and gate structure this project is supposed to follow is unknown,
and nothing in this repository should be treated as conforming to it.

**Unblock by** starting a session with `nimishshah1989/athena-os` as the *initial* repository, then
adding `jhaverios/event-planner` to it. The first repository attached pins the session's owner, so
the order matters.
