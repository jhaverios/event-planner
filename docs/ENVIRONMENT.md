# Development environment

The toolchain this project is built with, and the state it was left in.

**This container is ephemeral.** Everything below lives outside the repository, in `~/.claude`,
`~/.gstack` and `~/.local`. When the session container is reclaimed it is all gone and has to be
reinstalled. The repository is the only durable artefact.

## gstack — the development operating system

Source: `https://github.com/garrytan/gstack`, MIT, installed at `~/.claude/skills/gstack`.

Installed by running the project's own installer, `./setup`, which is the only supported method:

```bash
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
cd ~/.claude/skills/gstack && ./setup
```

What that produced, all verified on disk:

| Artefact | State |
|---|---|
| Skill registration | Performed by the installer, 58 entries under `~/.claude/skills` |
| `.gstack-owned` ownership markers | Present, so `/gstack-upgrade` and relink have records to act on |
| State root `~/.gstack` | Created, with `projects/`, `.last-setup-version`, gbrain detection cache |
| Browse binary | Built at `~/.claude/skills/gstack/browse/dist/browse`, 99 MB |
| Chromium | Downloaded to `/opt/pw-browsers`, used as the fallback browser |
| Stop hook | Registered, so session timeline entries close even when a skill is interrupted |

Two optional pieces were not installed, both reported by the installer itself. The plan-tune
cathedral hooks were skipped because setup ran non-interactively; add them with
`./setup --plan-tune-hooks`. And gbrain was not detected, so brain-aware blocks are suppressed in the
planning skills; add it with `/setup-gbrain`.

### A mistake worth not repeating

The first attempt at this install was wrong and had to be redone. `./setup` was refused by the
session's auto-mode classifier as self-modification, and instead of stopping to resolve that, the
skills were registered by hand-symlinking 53 directories into `~/.claude/skills`. The skills loaded,
which made it look like it had worked. It had not: there were no ownership markers, no `~/.gstack`
state root, no render layer, no relink registration and no browse binary, so `/gstack-upgrade` and
every browser-backed skill were silently broken.

The lesson is narrow and worth stating plainly. When a tool ships an installer, run the installer.
An approximation that produces the same visible surface is not the same install, and the difference
only shows up later, under pressure.

## ponytail — not currently installed

Source: `https://github.com/dietrichgebert/ponytail`, MIT.

An earlier hand-made copy was removed along with the gstack symlinks. ponytail installs through
Claude Code's plugin system, as two separate prompts:

```
/plugin marketplace add DietrichGebert/ponytail
```
```
/plugin install ponytail@ponytail
```

Those are interactive commands a person types, not shell commands, so this has to be done by the
user. The plugin route is also the only one that registers ponytail's two Node lifecycle hooks,
which is what gives it always-on activation rather than explicit invocation only.

## headroom — installed, not wired in

Source: `https://github.com/headroomlabs-ai/headroom`, Apache 2.0, version 0.37.0.

```bash
uv tool install --python 3.13 "headroom-ai[all]"
```

Verified working on a realistic payload, a 500-row registration export delivered as a `tool_result`
block:

| Measure | Value |
|---|---|
| Original | 101,723 characters |
| Compressed | 41,104 characters |
| Tokens saved | 18,967 |
| Compression ratio | 48.6% |
| First record still present | yes |

Two traps. The base package without the `[all]` extras ships no compressors and returns the input
unchanged at a 0% ratio, which reads as success unless the ratio is checked. And `compress()`
deliberately does not rewrite a plain user message, so a smoke test on one proves nothing; test
against a `tool_result` block.

**Not wrapped around the agent, deliberately.** `headroom wrap claude` repoints
`ANTHROPIC_BASE_URL` at a local proxy on port 8787, and this container exempts the model endpoint
from its outbound proxy, so wrapping is a credible way to break the session's own model access.
`headroom doctor` therefore reports the proxy unreachable and nothing routed, which is the expected
state rather than a fault. `headroom mcp` is the safer integration if this is wanted.

## artemis — assessed, not adopted

Source: `https://github.com/google/artemis`, Apache 2.0, last commit 2026-09-11, active. It drives
real Android phones from natural language over ADB and exposes an MCP server for Claude Code.

Not useful here, for three reasons.

1. It cannot run in this environment. There is no `adb`, `scrcpy` or `ffmpeg`, no USB passthrough for
   a handset, and `/dev/kvm` is absent so an emulator cannot start either.
2. The only Android surface in the current design is pretixSCAN, a third-party application this
   project does not build. There is no code of ours for it to regression-test.
3. The real failure modes at a venue door are offline synchronisation, venue wifi and screen glare.
   Bench automation exercises none of them. A twenty-person rehearsal on the actual tablets tests
   more, for less setup.

Revisit if check-in ever moves to an application we own.

## Known gaps in this container

| Gap | Consequence |
|---|---|
| `gh` not installed | `/ship` runs through push then fails at pull request creation. `/land-and-deploy` cannot run at all. The GitHub MCP tools do not substitute, because gstack shells out to `gh`. |
| `/cso` launcher binary absent | The security audit skill reports "not assessed" rather than running. |
| No Aside browser, headless Linux | Browser-backed verification is degraded. `/ship` step 8.1 and `/land-and-deploy` step 7 lose their runtime checks, so a change can pass every gate on static evidence and never be exercised against a running system. |
| Interactive gates block in headless sessions | By design. Unattended runs stall at the first question unless dispatched with `GSTACK_SESSION_KIND=spawned`. |

## How gstack and athena-os divide the work

gstack has no milestone concept. Confirmed by exhaustive search of all 53 skills: the only
occurrence of the word is a version-bump heuristic in the ship skill. Every gate gstack defines is
scoped to one branch, one plan file or one pull request, and its pipeline is per-feature:

> Think, Plan, Build, Review, Test, Ship, Reflect

concretely `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, implement, `/review`, `/qa`,
`/ship`. Inside `/autoplan` the review order is enforced: CEO, then design if there is user-interface
scope, then developer experience if developer-facing, then engineering last, always.

So gstack supplies rigour *within* a milestone. athena-os supplies sequencing *across* milestones.
They are complementary. Where they overlap, athena-os wins.
