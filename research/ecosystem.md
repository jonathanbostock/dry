# Claude Code Context/Memory Management Ecosystem Survey

Date: 2026-08-23. Scope: survey of existing Claude Code plugins/tools for cross-session
memory (Class A) and in-session context-window management (Class B), excluding "Wet"
(covered by a sibling research effort). Local Claude Code CLI reference version: v2.1.241.

Method: `gh` CLI (authenticated) for repo/issue metadata, shallow clones into `/tmp/research/`
for actual code reading, WebFetch/WebSearch for blogs and docs. Live docs
(code.claude.com/docs/en/hooks, /plugins, /plugins-reference, /statusline) were fetched
directly rather than relied on from training memory, which is stale relative to 2026-era
Claude Code (many hook events below did not exist in older training data).

---

## Summary

We surveyed ~50 named tools/repos plus three curated-list marketplaces and the official
Anthropic plugin directory, dissecting the ~15 most important ones by cloning and reading
actual hook/source code rather than trusting README marketing (which was wrong or
overstated in several cases we caught directly — see below).

**Class A (cross-session memory) is crowded and largely solved-if-imperfect.** The
dominant player, **thedotmack/claude-mem** (91,582 stars — up from a secondhand "~46k"
report, traced to sustained ~2-month organic-looking growth, not conclusively verified
either way), is overwhelmingly Class A: per-tool-call AI compression into SQLite+Chroma
storage, injected at `SessionStart`. It has **no `PreCompact`/`PostCompact` hooks and no
mid-session pruning at all** despite doc claims otherwise (a documented case of doc/code
drift), and its tracker documents real, serious problems worth learning from: unbounded
token/cost overrun from background daemons, up to 260x storage duplication bugs, and a
community security audit rating it HIGH risk (unsanitized content flowing into
`additionalContext` — a plausible persistent prompt-injection vector — plus an
unauthenticated local HTTP API). Dozens of smaller Class A tools exist across a spectrum
from serious (mcp-memory-keeper, memsearch) to thin/unsubstantiated marketing (a "71.5x
fewer tokens" claim traced to zero methodology).

**Class B (in-session context-window management) — our actual target — is real but far
less crowded, and nobody has fully solved it.** The standout finding is
**mksglu/context-mode** (~20,100 stars, built in months): explicitly *not* cross-session
(deletes old sessions by design), it implements exactly the two mechanisms we most need —
(1) a `PreToolUse`/MCP-layer sandbox that diverts raw tool output to disk+index *before* it
ever reaches the context window, and (2) a deterministic (non-LLM), `PreCompact`-triggered
snapshot re-injected at `SessionStart(source=compact)`. Independently, **Ruya-AI/cozempic**
was surfaced by *two separate research passes* as the single closest existing analog to our
planned plugin — a guard daemon with tiered auto-prune-and-reload, "safe-point" protection
against reloading mid-tool-call, and a corrections-survive-compaction digest (despite
inflated popularity marketing that a star/download-count check debunked). We also confirmed,
mechanically, that **transcript surgery — rewriting the session JSONL and reloading via
`claude --resume`— is a real, working technique** with at least 10 independent
implementations that converged on the same safety rules the hard way, including one
documented case of real data loss from stubbing write-tool inputs (a concrete rule for what
*not* to do). Passive context-percentage visibility (statuslines) is a saturated, largely
solved commodity problem built on a native Claude Code field — not a place to differentiate.
The single biggest piece of unclaimed design space found anywhere in this survey: **the
`PostCompact` hook has zero known adopters**, despite being the docs-sanctioned, direct
mechanism for exactly the post-compaction re-injection problem every "handoff" tool is
otherwise reconstructing indirectly.

---

## Ground truth: current Claude Code hook/plugin surface (verified live, 2026-08-23)

This section grounds everything below — it's what a plugin *can* mechanically do as of
CLI v2.1.241, per code.claude.com (docs.claude.com redirects there).

### Hook events (full current list)

Session lifecycle: `SessionStart` (matcher: `startup|resume|clear|compact|fork`), `Setup`,
`SessionEnd` (matcher: `clear|resume|logout|prompt_input_exit|other`).

Per-turn: `UserPromptSubmit` (can block), `UserPromptExpansion` (can block), `Stop` (can
block), `StopFailure`.

Tool execution: `PreToolUse` (can block), `PostToolUse`, `PostToolUseFailure`,
`PostToolBatch` (can block, fires after a full parallel tool batch resolves),
`PermissionRequest`, `PermissionDenied`.

Context/file: **`PreCompact`** (matcher: `manual|auto`; can block; **output/additionalContext
is explicitly NOT preserved after compaction** — docs say use it only to log/export/alert,
not to inject persistent context), **`PostCompact`** (fires after compaction; CAN return
`additionalContext` — this is the hook to use for post-compaction re-injection), `FileChanged`,
`CwdChanged`, `DirectoryAdded`, `InstructionsLoaded`.

Config/state: `ConfigChange` (can block), `Notification`.

Worktree: `WorktreeCreate` (can block), `WorktreeRemove`.

Subagent: `SubagentStart`, `SubagentStop` (can block), `TeammateIdle` (can block).

Task: `TaskCreated` (can block), `TaskCompleted` (can block).

MCP/elicitation: `Elicitation` (can block), `ElicitationResult` (can block).

Display: `MessageDisplay`.

Key design implication: **`PreCompact` -> snapshot -> `PostCompact` or
`SessionStart(source=compact)` -> re-inject `additionalContext`** is the sanctioned,
docs-supported pattern for lossless-compaction checkpointing. `PreCompact` receives
`transcript_path`, so a hook can read the actual conversation JSONL at compaction time
(several surveyed tools incorrectly assume this is not possible, or don't use it).

### Plugin structure

A plugin is a directory with `.claude-plugin/plugin.json` (name/description/version/author;
only this manifest file goes inside `.claude-plugin/` — everything else is at plugin root):
`skills/` (SKILL.md dirs, model-invoked), `commands/` (flat markdown, legacy), `agents/`,
`hooks/hooks.json` (or inline in plugin.json), `.mcp.json` MCP servers, `.lsp.json` LSP
servers, **`monitors/monitors.json`** (background processes started automatically alongside
the session — stdout lines delivered to Claude as notifications; `when: "always"` or
`"on-skill-invoke:<skill>"`; runs unsandboxed for the life of the session), `bin/` (added to
Bash PATH), `settings.json` (can force an `agent` as the main-thread persona). Hook types
include `command`, `http`, `mcp_tool`, `prompt` (LLM-evaluated), and `agent` (agentic
verifier) — not just shell commands.
Distribution: marketplaces (`anthropics/claude-plugins-official`, curated by Anthropic;
`anthropics/claude-plugins-community`, review-gated public catalog) or `--plugin-dir` /
`--plugin-url` for local/CI testing.

### Statusline

Native, built-in (no plugin required): statusline scripts receive JSON on stdin including
`context_window.used_percentage`, `.remaining_percentage`, `.total_input_tokens`,
`.total_output_tokens`, `.context_window_size` (200k default / 1M extended), and
`exceeds_200k_tokens`. `context_window.current_usage` is `null` before the first API call
and again after `/compact` until the next call repopulates it. There's also a built-in
transient "context-low" warning notification. **Implication: passive context-percentage
visibility is already a solved, native, zero-plugin-needed problem** — any tool whose main
value-add is "shows you a context bar" is mostly re-skinning a native feature; the real
open ground is *active* management (pruning, checkpointing, judgment-driven cleanup).

---

## Class A tools (cross-session persistent memory)

### thedotmack/claude-mem — 91,582 stars (as of 2026-08-23)

**The ecosystem's flagship, and overwhelmingly Class A** — its Class B footprint is
essentially nil for Claude Code specifically (see verdict below). Clone:
`/tmp/research/claude-mem`, HEAD at release v13.15.3.

**Metadata:** created 2025-08-31 (11.8 months old), Apache-2.0, ~122 contributors, 274 open
issues, ~3,443 cumulative issues+PRs filed in under a year (issue numbers already past
#3700), 10 releases in the 4 weeks before this survey — a very fast-moving, high-churn
project.

**Star-count discrepancy investigated (46k reported vs 91,582 measured):** traced to a
dated source — an Augment Code blog post (published 2026-04-07, updated 2026-06-18)
reporting "46.1K stars" from a screenshot. A third-party plugin directory snapshot 2 days
before this survey showed 89,387 stars; today, 91,582 — i.e. a continuous, still-accelerating
46.1k -> 89.4k -> 91.6k growth curve over ~2 months, independently corroborated by
trendshift.io showing recurring #1-trending placement across an 8-month span (not a single
spike). **This pattern argues against a one-off fake-star injection** (which typically shows
as a vertical one-day burst) though it doesn't rule out sustained manipulation either.
**The single most diagnostic test — pulling stargazer timestamps to check for burst
patterns — was attempted and blocked** (`gh api` with the star+json media type 404'd on
every page; unauthenticated returned 401), so this remains **[unverified]** rather than
cleared. One genuine, verifiable risk factor: claude-mem's README promotes a Solana crypto
token ("CMEM," with DEX/MEXC links); a community member flagged this as a red flag in
[issue #1212](https://github.com/thedotmack/claude-mem/issues/1212), and the maintainer
dismissed it as "unrelated to the codebase" without addressing the incentive question.
Star-farming is a documented tactic for crypto-adjacent projects generally, but no direct
evidence of it was found for claude-mem specifically — treat the star count as a strong but
not fully audited popularity signal.

**Hook events actually used** (read from the live `plugin/hooks/hooks.json`, which turned
out to materially disagree with the project's own architecture docs — see below): `Setup`
(pre-install version check), `SessionStart` (matcher `startup|clear|compact` — starts the
local worker daemon and injects prior-session context), `UserPromptSubmit` (creates/updates
the DB session row, saves the prompt), `PostToolUse` (async — queues the tool call for AI
compression into a stored "observation"), `PreToolUse` on `Read` only (async — injects prior
context about the specific file about to be read), `Stop` (async — triggers an AI-generated
end-of-turn session summary). **No `PreCompact`, no `PostCompact`, no `SessionEnd`** (grepped
for all three across the whole repo — zero matches for the first two; the docs describe a
"5-stage hook system" including a `SessionEnd`-registered `cleanup-hook.js`, but that file
and four of its five documented siblings **do not exist anywhere in the current codebase** —
a concrete, reproducible case of doc/code drift in a fast-moving project, worth treating
every other doc claim about internals with matching skepticism).

**Storage:** two coexisting subsystems. SQLite (via `bun:sqlite`, WAL mode) at a **global,
not project-local**, `~/.claude-mem/` directory (single DB shared across all projects,
scoped by a `project` column) — plus a second, only-partially-reconciled schema surface with
`teams`/`team_members`/`api_keys`/`audit_log` tables that appears to back the hosted
multi-tenant product rather than the local plugin. A Chroma vector DB
(`~/.claude-mem/chroma`) for semantic search, backed by a locally `uvx`-spawned Python
`chroma-mcp` process (or an optional remote Chroma server); hybrid search blends SQLite FTS5
with Chroma vector similarity.

**AI compression mechanism & cost:** triggered on every non-skip-listed tool call
(`PostToolUse`, async) and every `Stop` event (async) — fire-and-forget, non-blocking.
**Not a full-transcript re-read**: each observation call sends only that one tool's
input/output, capped at 16,000 chars per field (60% head / 30% tail / 10% elided-marker
split, added specifically after a bug where a 130k-char file blew the observer's context and
aborted the session) — cost scales with tool-call/turn count, not transcript length.
**Default model is `claude-haiku-4-5-20251001`**, billed to the user's own configured API
credentials, with genuine tiered routing (simple/fast tasks -> Haiku, "smart" tier ->
Sonnet). Alternate providers exist (Gemini Flash; OpenRouter, whose *default* model is
literally a free-tier model — a plausible cause of a cluster of very recent
quota-exhaustion complaints). A separate, optional hosted `cmem.ai` "Cloud Sync"/Pro product
exists alongside the free local pipeline, actively funneled from the installer via a 7-day
trial.

**SessionStart injection:** not a fixed-size dump — bounded by configurable caps
(session count, total-observation count, full-observation count), assembled from a compact
timeline index, a small number of "full" observation entries, the latest session summary,
and prior-session messages; an unbounded "full" mode exists but is opt-in only, not the
default automatic path. Real, computed token-economics telemetry
(`tokens_injected`/`tokens_saved_vs_naive`) is attached, with a code comment stating intent
to exclude actual memory content from that telemetry (not independently verified
end-to-end). Delivered via `hookSpecificOutput.additionalContext`, invisible to the user
since Claude Code 2.1.0.

**Class A vs Class B verdict — explicit:** overwhelmingly Class A. Zero `PreCompact`/
`PostCompact` hooks, zero mid-session tool-output pruning/trimming (grepped; only
whitespace-`.trim()` string utility hits). The one incidental touchpoint: `SessionStart`'s
matcher includes `compact`, so its ordinary cross-session memory-injection routine re-runs
after compaction too — that's Class A memory re-injection triggered by a Class B *event*,
not compaction-quality work, tool-result cleanup, or context budgeting of the live window.
Notably, for a *different* backend it also supports (OpenCode), claude-mem **does** wire a
real compaction-specific hook (`experimental.session.compacting`) — so the team has both the
interest and the capability, they simply haven't built the equivalent for Claude Code's
`PreCompact`/`PostCompact` as of this clone.

**Known complaints/issues (tracker-sampled, most substantive threads read in full):**
- **Cost/token overuse:** background "observer SDK sessions" kept burning tokens even with
  Claude Code fully closed ([#2336](https://github.com/thedotmack/claude-mem/issues/2336),
  consolidated into root-cause tracker [#2378](https://github.com/thedotmack/claude-mem/issues/2378));
  one 20x-Max-plan user reported burning 25% of their limit in a couple of hours.
- **Duplicate/stale memory & storage bloat:** a watermark bug caused the same docs to be
  re-added every sync cycle — **74 GB of Chroma storage for ~27,000 logical rows (~260x
  duplication)** ([#3591](https://github.com/thedotmack/claude-mem/issues/3591)); unbounded
  HNSW index growth from delete+add conflicts ([#3266](https://github.com/thedotmack/claude-mem/issues/3266)/[#3268](https://github.com/thedotmack/claude-mem/issues/3268)); near-duplicate observations from byte-identical-only dedup ([#3038](https://github.com/thedotmack/claude-mem/issues/3038)/[#3063](https://github.com/thedotmack/claude-mem/issues/3063)).
- **Performance/CPU:** repeated `chroma-mcp` CPU storms (60-307% CPU reported across
  Windows/macOS, [#2220](https://github.com/thedotmack/claude-mem/issues/2220)/[#2195](https://github.com/thedotmack/claude-mem/issues/2195)/[#2253](https://github.com/thedotmack/claude-mem/issues/2253)),
  worker hangs after macOS sleep/wake ([#3340](https://github.com/thedotmack/claude-mem/issues/3340)),
  a feature explicitly *cut* by the maintainer for being "slow, needs tokens, not always
  useful" ([#3455](https://github.com/thedotmack/claude-mem/issues/3455)).
- **Install/compat, especially Windows:** slow cold-boot exceeding editor init deadlines,
  silent daemon-spawn failures, native-module build failures (Bun + `uvx`-spawned Python +
  multiple `tree-sitter-*` parsers is a heavy, fragile native-dependency surface).
- **Privacy tag gaps:** manual `<private>` redaction had shipped bugs where tagged content
  still reached the AI summarizer ([#2204](https://github.com/thedotmack/claude-mem/issues/2204)/[#2149](https://github.com/thedotmack/claude-mem/issues/2149));
  automatic secret redaction is still an open feature request
  ([#2616](https://github.com/thedotmack/claude-mem/issues/2616)).
- **Security — the most substantive document on the tracker:** a community security audit
  ([#1251](https://github.com/thedotmack/claude-mem/issues/1251), 23 findings, rated HIGH
  overall) reports: **CRITICAL** — stored-observation content is injected into
  `SessionStart`'s `additionalContext` with no sanitization/escaping (independently
  confirmed by direct code read: plain template-literal interpolation into XML-ish tags),
  i.e. a plausible persistent prompt-injection vector; **HIGH** — arbitrary filesystem read
  via `smart_unfold`/`smart_outline`/`smart_search` MCP tools (no path allowlisting);
  **HIGH** — the local worker's HTTP API is entirely unauthenticated by default and can be
  reconfigured to bind `0.0.0.0`; **HIGH** — the installer does `curl | node` with no
  checksum/signature verification; **MEDIUM** — plaintext API keys, full `process.env`
  inherited by the unauthenticated daemon. The maintainer closed the issue as "not a bug or
  feature request" with no visible point-by-point remediation — **treat the critical
  prompt-injection finding as not confirmed fixed.** (Mitigating factor: the compression
  agent itself runs with `disallowedTools: ['Bash','Read','Write',...]`, limiting blast
  radius.)

**Other notes:** telemetry exists with a stated (not independently verified) content-scrubbing
policy; `npm install -g claude-mem` alone does *not* register hooks (must use the CLI
installer or the plugin marketplace flow); Apache-2.0 base license with a separate
commercial boundary for the hosted Pro product.

Sources: https://github.com/thedotmack/claude-mem ; issues cited above; live docs at
https://code.claude.com/docs/en/hooks (used to verify claude-mem's hook usage against the
current, much larger hook vocabulary than older training data would suggest).

### zilliztech/memsearch — 2,494 stars, very active

**Class verdict: pure Class A.** No PreCompact hook, no token/context-window logic, no
mid-session pruning found anywhere in the codebase.

**Mechanism:** hooks + a forked-subagent skill, deliberately *not* MCP. Plugin manifest at
`plugins/claude-code/.claude-plugin/plugin.json`; `hooks/hooks.json` registers:
- `SessionStart` (sync): starts a `memsearch watch` indexer singleton; injects the last 2
  daily markdown logs (<=40 lines each) as `additionalContext`.
- `UserPromptSubmit` (sync): does **no search** — just a static "[memsearch] Memory
  available" systemMessage.
- `Stop` (async, 120s budget): summarizes the last turn via `claude -p --model haiku
  --no-session-persistence`, appends bullets to `.memsearch/memory/YYYY-MM-DD.md`, reindexes.
  Cost: one Haiku call per assistant turn.
- `SessionEnd` (backgrounded): stops watcher.

Retrieval is a `context: fork` skill (`skills/memory-recall/SKILL.md`) — runs in a forked
subagent that shells to `memsearch search` -> `expand <hash>`, returning only a curated
summary to the main context. That's a context-economy *architecture* choice (avoids a
persistent MCP tool-schema tax and search noise in the main thread), not runtime budgeting.

**Storage:** Markdown daily logs are the source of truth; Milvus (dense + BM25 sparse,
RRF fusion) is an explicitly rebuildable derived cache, chosen for crash-safety,
portability, and git-friendliness. Default embedder is local ONNX bge-m3 (no API cost).
A separate `memsearch compact` LLM-compresses the *stored corpus*, not the live session —
distinct from Class B compaction.

**Issues (gh):** #692 orphaned watch processes (34 leaked / 6.7GB); #676 SessionStart
breaching its own 10s timeout; #684 recency preview truncating the newest entries; #681
shell injection in an OpenCode adapter; #664 (fixed) Stop hook dumping raw transcripts past
a 128KB argv limit; #629 full reindex every turn on Milvus Lite.

**Credibility flag:** Milvus's own blog posts overstate the mechanism — one claims
UserPromptSubmit does semantic search + top-3 injection (contradicted by the shipped code);
another describes an "Auto Dream" consolidation process and a "KAIROS daemon" from "leaked
Claude Code source" with zero corroboration in the repo — treat as marketing embellishment,
trust the code.

Sources: https://github.com/zilliztech/memsearch ;
https://milvus.io/blog/adding-persistent-memory-to-claude-code-with-the-lightweight-memsearch-plugin.md ;
https://milvus.io/blog/claude-code-memory-memsearch.md

### julep-ai/memory-store-plugin — 9 stars

**Class verdict: primarily Class A, with an attempted Class B (PreCompact) hook that is
mechanically broken.**

**Mechanism:** hooks + a remote hosted MCP server (`https://beta.memory.store/mcp` via
`npx mcp-remote`, OAuth 2.1), bridged by a file queue — and the bridge is unfinished.
`hooks/hooks.json`: SessionStart, SessionEnd, PreToolUse(Write|Edit), PreToolUse(Bash),
**PreCompact** -> `scripts/save-context.sh`, Notification.

**Hard dependency on hosted backend** — every read/write terminates at
`beta.memory.store` (requires GitHub/Google OAuth); no local storage beyond a transient
queue file; no self-host option.

**Instructive failure:** hooks are bash and can't call MCP tools directly, so the project
burned through 4 architectures in one day per its CHANGELOG: (1) `claude mcp call ...` — a
CLI subcommand that **does not exist** (self-documented in KNOWN_ISSUES.md); (2) magic
strings in `additionalContext` hoping a skill would notice — didn't work; (3) current:
hooks append to `.memory-queue.jsonl` and a `proactive: true` skill is *supposed* to drain
the queue on every user message and call an MCP tool. Migration is incomplete: 5 scripts
still reference the dead CLI pattern, **including the PreCompact hook itself**, all
silently no-op'ing behind `|| true`. Even "working," `save-context.sh` never reads the
actual transcript — it proxies "context" via git log/diff/TODO greps, apparently because
the authors believed the conversation was inaccessible to a PreCompact hook (this is false
per live docs — PreCompact receives `transcript_path`).

**Value to us:** a catalogue of anti-patterns — hosted-only storage, hook-to-MCP impedance
mismatch, "trust the model to run the queue-processor skill every message" as the only
guarantee, dead code paths that exit 0 so failures are invisible.

Sources: https://github.com/julep-ai/memory-store-plugin

### mkreyman/mcp-memory-keeper — 134 stars, last push 2026-06-06

**Class verdict: overwhelmingly Class A; one minor Class B-adjacent lever (tool-schema
footprint control).**

**Mechanism:** pure MCP server, zero hooks (repo-wide grep for "hook" finds only a git
pre-commit doc). Single TypeScript server (~5,700 lines) on the MCP SDK; storage is SQLite
via better-sqlite3 at `~/mcp-data/memory-keeper/context.db`, WAL mode. Exposes **44 tools**:
session lifecycle, save/get, checkpoints (`context_checkpoint`/`context_restore_checkpoint`),
`context_prepare_compaction`, `context_summarize`, `context_compress`, search
(plain/all/semantic), export/import, knowledge-graph extras, multi-agent delegate,
journal/timeline, channels, batching, poll-based `context_watch`.

**Proactive/pull only — not automatic.** `context_prepare_compaction`'s name suggests
automation but only describes internal behavior once the agent calls it; nothing wires it
to the real Claude Code `PreCompact` event. The README is candid: "When you notice the
conversation getting long, YOU ask Claude to save a checkpoint... When Claude runs out of
space and starts fresh, YOU tell it to restore." The recommended "automation" is a CLAUDE.md
instruction telling Claude to call the tools proactively — a soft guarantee at best.

**Token logic exists but caps MCP *response* size** (to avoid blowing the transport limit,
born from a 26,866-token single response bug report) — not live session-window awareness.
The genuinely useful pattern: a `TOOL_PROFILE` env var restricts which of the 44 tool
schemas are advertised at all, because users noticed the schema block itself is a standing
per-turn context tax. **Design lesson: every MCP tool schema is context overhead on every
turn; keep tool surface minimal or prefer hooks/skills over a large persistent MCP server.**

**Issues:** arbitrary-file-read vuln via `context_import.filePath` (fixed); SQLITE_CANTOPEN
when parent CWD changes; checkpoint-restore creating a *new* session instead of restoring
(correctness bug in the headline workflow); lossy checkpoint export/import (open).

Sources: https://github.com/mkreyman/mcp-memory-keeper

### Other Class A tools (quick-pass)

**coleam00/claude-memory-compiler — 1,280 stars.** Real plugin: `SessionStart` +
**`PreCompact`** + `SessionEnd` hooks (`uv run python hooks/*.py`). SessionStart injects a
knowledge index + latest daily log. PreCompact/SessionEnd parse the transcript (via
`transcript_path`), grab the last ~30 turns, and launch a **non-blocking background**
process (must finish in <10s) that uses the **Claude Agent SDK** to LLM-extract
keep-worthy content into daily markdown logs; a separate compiler organizes dailies into
cross-referenced knowledge articles (Karpathy-KB style). Storage: plain markdown, no DB.
**Primarily Class A with a genuine Class B trigger** — a real checkpoint-before-compaction
side-channel so detail survives summarization, though it doesn't alter compaction itself,
prune tool output, or track token budget. Probably the best small reference implementation
of the async-PreCompact-snapshot pattern found in this survey.

**lucasrosati/claude-code-memory-setup — 947 stars.** Not a plugin at all: no
hooks.json/plugin.json/MCP — a README-guide plus two scripts (Obsidian vault conventions in
CLAUDE.md; `/resume`/`/save` are not real slash commands, just strings CLAUDE.md tells
Claude to interpret; a cron export pipeline wrapping a third-party conversation extractor;
depends on third-party "Graphify" for code graphs). **Pure Class A, weakly implemented.**
The headline "71.5x fewer tokens" claim is unsubstantiated — appears twice with no
benchmark or methodology; the repo's own only data table reports a *different* number
(499x) for a *different* project (Graphify itself), i.e. the marketing headline is
disconnected from its own cited data point.

**obra/claude-memory-extractor — 118 stars.** Manual CLI (not a plugin, no
manifest/hooks) by Jesse Vincent ("obra"). One subcommand reads
`~/.claude/projects/*.jsonl`, chunks it, and shells a full `claude --model sonnet --print`
subprocess per chunk, writing markdown memories. Real cost: README estimates
~$0.50-2.00/conversation. Author's own README disclaimer: hacked together in a week,
not closely reviewed. **Write-only — no injection/recall mechanism exists at all**
(explicitly on the roadmap). Class A, and only half of it implemented.

**raiyanyahya/recall — 742 stars.** Real plugin: `SessionStart` (matcher
`startup|resume|clear`), `Stop` -> byte-offset-tracked transcript append to
`.recall/history.md`, `SessionEnd`, plus `/recall:save`. Digest regenerated via a vendored
TF-IDF + TextRank summarizer (numpy path with a provably rank-identical stdlib fallback).
**"Entirely offline" claim verified** — zero external API calls found anywhere in the
scripts. Notably careful SessionStart injection: content is fenced as "SAVED REFERENCE
DATA... untrusted" with an explicit prompt-injection warning — a good pattern to borrow
regardless of class. **Class A only.**

**Durafen/Claude-code-memory — 75 stars.** Python indexer with real tree-sitter parsing
and real Qdrant vector storage, but needs a Voyage/OpenAI key for embeddings (not offline
despite self-hosting); actual Claude Code integration lives in a *separate* repo
(mcp-qdrant-memory) plus a Docker Qdrant instance — heavy install, legacy raw
settings.json hooks (no `.claude-plugin/`). A "Memory Guard" fires a subprocess Claude call
on **every single Write/Edit/MultiEdit** to check the knowledge graph for duplicates — a
real per-edit latency/cost tax. **Class A**, with a Class-B-adjacent PreToolUse gate that is
really code-quality policing, not context management. Zero GitHub issues filed (likely low
real-world usage).

### Additional Class A tools surfaced by the ecosystem survey (awesome-claude-code, official marketplace, topic search)

Quick-resolved from an original "CHECK" shortlist plus new topic-search hits — all
confirmed Class A (or out of scope), each read enough to state mechanism/verdict with
confidence:

- **agenticnotetaking/arscontexta** (3,481 stars) — generates a personal Obsidian-style
  vault from a conversational interview. Note-taking/PKM system, not compaction.
- **rohitg00/pro-workflow** (2,777 stars) — SQLite-backed self-correction rules
  (FTS5-searchable, auto-loaded at SessionStart), persistent research wikis, auto-research
  loop. Mentions "compaction-aware state" as one quality-gate hook — a minor Class B nod on
  top of an otherwise Class A tool.
- **activeloopai/hivemind** (1,579 stars) — captures session traces, distills them into
  shareable `SKILL.md` files across a team; hybrid lexical+semantic search; YC-backed,
  benchmarked on LoCoMo (claims 25% cheaper, 1.7x fewer tokens vs. no shared memory). Team
  knowledge sharing, not in-session management.
- **nagisanzenin/engram** (1,366 stars) — **correction to an earlier hypothesis: this is
  out of scope entirely.** Its own README states it is *not* an agent-memory plugin — it's
  an FSRS spaced-repetition system that quizzes the *human* so they retain what the agent
  explained. Neither Class A nor B.
- **tigerless-labs/autoharness** (1,185 stars) — distills and merges skills from sessions,
  **prunes skills that stop being used**. This is skill-lifecycle pruning, not
  context-window pruning — a different problem despite the keyword overlap.
  Class A.
- **SethGammon/Citadel** (910 stars) — "routes requests, preserves repository state between
  sessions, coordinates parallel work, records evidence and handoffs." Class A/B hybrid
  worth a closer look later given the explicit handoff+evidence-recording angle and the
  cost-telemetry feature.
- **ReflexioAI/claude-smart** (771 stars) — turns corrections into project/shared skills.
  Its own README **directly benchmarks against claude-mem** ("~3x better at turning
  corrections into rules Claude follows, ~50% more guidance retained," self-reported in its
  own `EXPERIMENT.md`) — a vendor claim, not neutral evidence, but notable as the clearest
  claude-mem comparison found anywhere in this survey.
- **kevin-hs-sohn/hipocampus** (1,366 stars) — primarily Class A, but its "compaction tree"
  + `ROOT.md` hierarchical index for session summarization (benchmarked on a 900-question
  implicit-recall set, claims 21.6x over BM25/vector search) is a legitimately novel data
  structure worth studying for how *we* might structure checkpoint documents, independent of
  its cross-session use case.
- **Official marketplace (`anthropics/claude-plugins-official`) Class A entries:**
  **claude-md-management** (first-party, Anthropic-authored — audits/keeps CLAUDE.md
  current) and **remember** (external, pulled from `Digital-Process-Tools/claude-remember`
  — extracts/compresses conversations into tiered daily logs).

---

## Class B tools (in-session context-window management)

### "Context Mode" (mksglu/context-mode) — ~20,100 stars

**THE HEADLINE FINDING OF THIS SURVEY: a genuinely Class B tool, and the closest existing
analog to our planned plugin.** Covered by MindStudio blog posts comparing it to claude-mem
(the posts frame Context Mode and claude-mem as orthogonal/complementary — within-session
vs between-session). Independent dev (Mert Koseoglu), **Elastic License 2.0** (source-
available, not OSI-open — read for ideas, don't copy code wholesale). A near-identical
1-star rebrand exists at scottconverse/context-mode with lower claimed numbers; ignored as
derivative.

**Explicitly NOT Class A** — README states that without `--continue`, previous session
data is deleted immediately; SessionStart's `startup` branch actively wipes sessions older
than 7 days. An open issue on its tracker is a third party asking for the very Class-A
layer it deliberately omits.

**Mechanism — two distinct Class B levers:**
1. **Pre-emptive bloat avoidance** (source of its "315KB -> 5.4KB / ~98% reduction"
   headline number): an MCP server exposes ~11 tools (`ctx_execute`, `ctx_execute_file`,
   `ctx_batch_execute`, `ctx_index`, `ctx_search`, `ctx_fetch_and_index`, plus
   stats/doctor/upgrade/purge/insight). A `PreToolUse` hook routes Bash/Read/Grep/WebFetch/
   Agent calls through this sandbox; raw output is written to a content-addressed on-disk
   store + FTS5/BM25 index, and only a small computed result (often a few hundred bytes)
   enters the live context. This is tool-output pruning at the point of creation, not
   after-the-fact cleanup.
2. **Lossless compaction survival:** a `PreCompact` hook builds a structured snapshot from
   a SQLite event log and stores it; `SessionStart` with `source: "compact"` re-injects it
   via `additionalContext` — i.e. exactly the PreCompact-snapshot -> SessionStart(compact)
   re-injection loop that the live hook docs recommend.

**Correcting the marketing:** MindStudio's blog says old context is "sent to Claude (or a
smaller model) for summarization" — **false per the actual code**. The snapshot builder is
a pure deterministic function (no LLM call anywhere in the codebase) that buckets state
(files/errors/decisions/rules/git/tasks/...) into a compact XML block where each section
carries a ready-to-run `ctx_search()` call to rehydrate detail on demand — "a table of
contents, not a compressed transcript." Zero LLM API calls means zero incremental token/API
cost for the checkpointing mechanism itself. Storage: better-sqlite3. One hosted/telemetry
bit (`ctx_insight` opens an analytics dashboard at context-mode.com) — opt-in, unrelated to
the memory mechanism.

**Issues:** duplicate hook injection on cache-heal; uninstall leaves the SessionStart hook
and MCP server process running; plan-mode blocks batch execution; various adapter routing
gaps. Very active tracker (15+ issues in 48h at time of research) — young and churning
fast.

Sources: https://github.com/mksglu/context-mode ;
https://www.mindstudio.ai/blog/claudemem-vs-context-mode-claude-code-memory-plugins ;
https://www.mindstudio.ai/blog/context-mode-claude-code-315kb-to-5kb-session-compression

### coleam00/claude-memory-compiler's PreCompact hook

See Class A section above — listed there since the tool is primarily Class A, but its
async PreCompact-triggered transcript-snapshot pattern is a legitimate, working, minimal
Class B mechanism worth reusing as a reference implementation.

### Transcript surgery + `claude --resume` — VERIFIED REAL, active mini-ecosystem (~10 implementations)

**This resolves the mission's key open question: rewriting the session JSONL and reloading
via `claude --resume`/`-r` is a genuine, working pattern, not vaporware** — at least 10
independent implementations converged on the same safety rules, several the hard way (via
filed data-loss/corruption bugs). It is fragile in specific, well-documented ways and fights
Claude Code's own opaque server-side internals, but it works.

**Convergent safety rules (independently rediscovered by multiple authors — treat as
load-bearing design constraints for anything we build here):**
1. **Never mutate the original JSONL — always fork to a new file with a new session UUID**
   and hand back a `claude --resume <new-sid>` command. Two independent reasons given:
   rollback safety, and Claude Code's prompt cache is keyed to the old prefix, so in-place
   edits show a stale `/context` reading until a new session ID forces a fresh cache slot.
2. **`tool_use`/`tool_result` must be deleted as an atomic pair, matched by ID** — never
   positionally, since parallel tool calls break positional pairing.
3. **The `parentUuid` chain must be re-stitched** after any drop (walk up to the nearest
   surviving ancestor), or resume renders only a fragment of the conversation.
4. **`thinking` blocks are dangerous to touch selectively.** The `signature` field is the
   server's encrypted full reasoning content, not a hash — deleting it while keeping the
   rest of a turn can degrade reasoning quality or get the request rejected. Converged rule:
   only delete a thinking block if deleting its *entire* turn, never in isolation.
5. **Never stub/replace `tool_use.input` for write-capable tools** (Write/Edit/MultiEdit/
   NotebookEdit) **— this is the single most important negative finding.**
   [CosmoNaught/claude-code-cmv](https://github.com/CosmoNaught/claude-code-cmv) (83 stars,
   has an arXiv paper, CI+codecov) stubs large write-tool inputs during trim; a filed
   [issue #11](https://github.com/CosmoNaught/claude-code-cmv/issues/11) reports this
   **silently corrupted real project files** — a trimmed transcript got replayed/
   re-serialized later, and the model wrote the `[Trimmed input: ~N chars]` placeholder
   back to disk as if it were real file content, with the tool call still reporting
   success; one file was reduced to nothing but the placeholder and had already been
   committed to git with no other copy. **Verdict: surgery on tool *outputs*/results is
   broadly safe; surgery on tool *inputs* for write-capable tools is a live data-loss
   hazard.**
6. **Live-rewrite-while-the-CLI-has-the-file-open is a real race condition.**
   [Ruya-AI/cozempic](https://github.com/Ruya-AI/cozempic) issue #106 ("guard daemon
   rewrites the live transcript out from under Claude Code") was fixed via "safe-point"
   detection that defers reload during in-flight tool calls/subagents.
7. Treat `compact_boundary` / `isCompactSummary` markers in the transcript as untouchable.

**Implementations (all shallow-cloned and code-read, not just README claims):**
- **Mor-Li/sculptor** (3 stars) — cleanest, most principled: `s1.py` converts JSONL to
  editable Markdown (`### turn N · kind · bNNNN · tokens` + MD5-hashed sidecar JSON), a
  *dedicated subagent* (recommended: Opus, so the main conversation doesn't pay the
  editing-context cost) edits the Markdown, `s2.py` reconciles by MD5 diff into a new JSONL
  + `edit-manifest.json` audit trail and prints a ready `claude --resume` command. Correctly
  handles tool_result stubbing vs tool_use hiding, parentUuid re-stitching, and explicitly
  documents `compact_boundary` as do-not-delete. Companion to a claimed ICLR 2026 paper
  (arXiv:2508.04664). Low adoption (3 stars) — a solid research prototype, not
  battle-tested at scale.
- **zhurong666/claude-code-session-editor** ("cc-session", Rust) — TUI + scriptable CLI,
  dedicated pairing/atomic-write modules, fork-only (atomic tmp -> fsync -> rename). Standout
  feature: a `heatmap` command ranking turns by *true* tiktoken count of the whole raw JSONL
  line (text + tool args + stdout + metadata) — "2-3x larger than the old text-only
  estimate." Directly useful reference for a context-budgeting UI. 0 stars/issues disabled —
  unproven at scale but careful code.
- **CosmoNaught/claude-code-cmv** ("CMV") — most feature-complete: git-like
  snapshot/branch/trim/tree/export semantics over context state, a TUI dashboard,
  `PreCompact` + `PostToolUse` auto-trim hooks, a `cmv benchmark` command modeling
  prompt-cache-miss cost of trimming. 83 stars, real users, real bugs (issue #10: trimmed
  JSONL treated by `--resume` as an empty ~2%-context session, open; issue #11 above). Good
  "virtual memory for context" framing, but the concrete cautionary tale on write-tool-input
  stubbing.
- **Ruya-AI/cozempic** — broadest single tool, spanning Class A/B/C/E at once: 18 composable
  pruning strategies (compact-summary-collapse, tool-result-age, tool-use-result-strip,
  image-strip, etc.) across gentle/standard/aggressive tiers; a guard daemon polling
  context% every 30s with 4-tier auto-prune-and-reload (25/55/80/90%) with safe-point
  protection; a `doctor` command repairing known corruption classes; a "behavioral digest"
  that extracts user corrections into Claude Code's native memory so they survive
  compaction. **Marketing credibility flag: README claims "100,000+ power users"/"100k+
  downloads," actual GitHub stars = 375, actual npm downloads last month = 525** — treat
  popularity claims skeptically, but the issue tracker (14 open, ~15 closed, active through
  2026-08-22) shows genuine usage, including a real fixed security issue (#123, PyPI
  auto-upgrade couldn't be disabled) and the real data-loss race (#106) above.
- **waterside0219/session-forge** — different strategy: sliding-window "keep last N tokens,
  snap forward to next real user message," with a post-write verification pass (event
  count, unbroken parentUuid, no UUID reuse) that aborts before touching the live tmux pane
  if anything looks wrong. Good cheap/robust fallback-mode pattern.
- **meridianix/clawdbot-session-pruner**, **javimosch/claude-session-optimizer** — simpler
  first+last-N-KB truncation. Note: javimosch's tool **unconditionally strips all `thinking`
  blocks** regardless of whether the surrounding turn survives — directly violates rule 4
  above, a plausible source of resume-quality degradation.
- **justinritchie/cowork-session-recover** — targets Claude's "Cowork" desktop feature
  (shares the same JSONL format); ships `repair_chain.py` addressing three chain-corruption
  classes it attributes to upstream Claude Code issues **#24304, #35024, #37437, #46603**
  (orphan parentUuid refs, file-history-snapshot messageId collisions, disconnected
  `/compact` roots).
- **mason0510/fix-jsonl** — fixes upstream issue **#10199** (truncated JSON after
  crashes/Ctrl-C).
- **mcpware/claude-code-organizer** (371 stars) — the "distiller" behind a widely-shared
  "70MB->7MB, 90% reduction" dev.to post; real extractive (non-LLM) per-tool-type rules,
  builds a `tool_use_id -> tool_name` map to correctly pair parallel tool calls.
- Lighter editors: **chetools/ChatJsonEditor** (multi-provider, whole-turn delete/undo),
  **didvc/claude-code-jsonl-editor** (Preact/Vite web UI).

**Upstream context any transcript-surgery design must account for** —
[ArkNill/claude-code-hidden-problem-analysis](https://github.com/ArkNill/claude-code-hidden-problem-analysis)
(a reverse-engineering writeup of Claude Code's own internals) documents still-open upstream
issues: non-atomic JSONL writes during concurrent tool execution can drop `tool_result`
entries and permanently break resume (meta-issue **#21321**, 10+ duplicates, plus **#41346**,
**#45286**, **#31328**); a server-controlled, GrowthBook-gated "microcompact" silently
replaces tool results with `[Old tool result content cleared]` with no notification; a
separate `applyToolResultBudget()` pre-request pipeline truncates tool results before that
even runs. **Also flagged for our PreCompact reliability assumption:**
[anthropics/claude-code#13572](https://github.com/anthropics/claude-code/issues/13572)
("PreCompact hook not triggered when /compact command runs") was filed and **closed as not
planned** — worth independently re-verifying against v2.1.241 before depending on PreCompact
firing for manual `/compact`.

**Overall verdict:** the safe design space is fork-never-mutate, pair tool_use/tool_result
by ID, stub only tool *results* (never write-tool *inputs*), leave thinking blocks alone
unless dropping the whole turn, and treat compaction-boundary markers as untouchable. This
is a legitimate, buildable core mechanism for our plugin, not a stretch feature — but it
should ride on top of, not fight, Claude Code's own (partially undocumented) transcript
handling.

### Handoff / checkpoint / session-continuity plugins — the most crowded niche in the ecosystem

Dozens of `claude-code-handoff-*`, `context-guard*`, `session-continuity` repos exist —
strong organic demand for exactly what we're building. Notable ones read in depth:

- **sofumel/claude-handoff-revive** — the most technically honest design found in this
  whole survey. Its PreCompact hook's own comments explain why it does *not* try to save
  state from inside the hook: "hooks cannot invoke Claude, and there is no inference turn at
  compaction time." Instead it **gates/blocks manual `/compact`** (via the hook's
  `{"decision":"block","reason":...}` contract) when unsaved-work markers show the user
  hasn't run its save skill yet — but explicitly never blocks *auto* compaction, since that
  risks wedging a session with no escape. Key constraint to internalize: a PreCompact hook
  is a plain shell command with no model access, so any "smart" state-saving must already
  have happened earlier (e.g. on `Stop`) or be handled via a gate-and-ask pattern.
- **u-ichi/compact-plus** — PreCompact -> backup-transcript-copy (plain `cp`, capped
  rotation) plus a manual `/compact-plus` skill writing a structured Markdown state file
  (Active Plan/Current Phase/TaskList/Decisions/Constraints/Worker Topology/Failed Attempts)
  to `$TMPDIR`, keyed by `$CLAUDE_CODE_SESSION_ID`.
- **EliaAlberti/cpr-compress-preserve-resume** — pure slash-command triad (`/preserve` +
  `/compress` + `/resume`, no hooks/code). Confirms `claude config set --global
  autoCompact false` is a real, disableable global config flag (corroborated independently
  by the ArkNill bug-tracker repo's references to `DISABLE_AUTO_COMPACT`) — i.e. fully
  manual compaction control is a verified, available escape hatch today.
- **Ricky-Stevens/context-guardian**, **Guard8-ai/ContextGuard**, and ~25 similarly-named
  `context-guard*` repos — a whole genre of "watch context%, warn/checkpoint/handoff before
  autocompact" tools; not all deep-dived given redundancy, but the genre size itself is a
  strong market signal.

### Statusline context-percentage / token-budget tools

- **ccusage/ccusage — 18,121 stars.** Primarily historical cost/usage analytics
  (`daily`/`blocks`/`session` reports) — that part is **Class A-adjacent** (retrospective,
  not live in-session). Its `statusline` subcommand (beta), however, is genuinely **Class
  B**: shows a live "brain 25,000 (12%)" context readout with configurable color thresholds,
  and per its docs "uses Claude Code's `context_window` data when available for accurate
  token counts," falling back to its own estimate otherwise — independent confirmation that
  the native statusline JSON payload carries `context_window`. **Be precise: ccusage-the-tool
  is Class A; ccusage's `statusline` command specifically is Class B.**
- **sirmalloc/ccstatusline — 12,527 stars.** Unambiguously Class B. Dedicated widgets:
  `ContextBar`, `ContextLength`, `ContextPercentage`, `ContextPercentageUsable`,
  `ContextWindow`. Its context-percentage code prefers Claude Code's own reported context
  metrics, falling back to a transcript-based estimate; a separate compaction-telemetry
  module scans the transcript for `{type:'system', subtype:'compact_boundary'}` records and
  reports compaction count, trigger (`auto`/`manual`/`unknown`), and `tokensReclaimed` from
  `compactMetadata.preTokens`/`postTokens` — a clean reference implementation for reading
  Claude Code's own compaction telemetry, independent of any third-party pruning logic.
- Dozens of smaller `claude-*-statusline`/context-warning repos exist (e.g. threshold
  warnings at 120k/150k/180k tokens) — this genre is saturated and mostly thin wrappers
  around the same native statusline JSON payload (see "Ground truth" section above).

### Tool-output pruning hooks

- **ZizzX/claude-output-trim** — `PostToolUse` on Bash/Grep only: strips ANSI, collapses
  repeated lines, keeps head(80)+tail(40). Best-practice detail worth copying: **the full
  original output is always saved to disk** (auto-pruned after 7 days) and the truncation
  marker tells the model exactly where to `Read` it back — truncation is framed to the model
  as reversible, not destructive.
- **PCIRCLE-AI/toonify-mcp** — confirms an important hook API detail: a `PostToolUse` hook
  can return **`updatedToolOutput`** to actually *replace* what the model sees (not just log
  or approve/deny). Notes that Codex CLI's hooks cannot do this yet, hence a separate
  pipe-filter mode for Codex — i.e. this is a Claude-Code-specific capability worth building
  around.
- **arbiterForge/codeArbiter** — a more elaborate `prune-transcript.py` (dry-run/audit/
  execute modes, liveness check that refuses to touch a transcript modified within the last
  N seconds), but per its own code comments hook-mode wiring is explicitly "wired in a later
  phase" — currently CLI-only. Heavy internal iteration visible (numbered ADRs) suggesting
  this is harder to get fully right even for a dedicated team.
- cozempic's `tool-output-trim` and `tool-result-age` strategies (age-based: recent results
  verbatim, mid-age JSON-minified, old ones stubbed) are also relevant here.

### Compaction-quality / custom `/compact` replacement — mostly a negative finding

**Oldrich333/hard-compact** claims a drop-in `compactSummaryPrompt` key in
`~/.claude/settings.json` overriding the compaction summarizer's instructions entirely.
**This could not be verified to actually exist in current Claude Code** — it's absent from
the official settings docs, and `gh search code` for `compactSummaryPrompt` only turns up
the string in unrelated third-party tools reusing it as their own internal variable name.
More tellingly, **two open Anthropic feature requests ask for exactly this capability**:
[#55905](https://github.com/anthropics/claude-code/issues/55905) ("Add `compactInstructions`
setting for default /compact arguments") and
[#14160](https://github.com/anthropics/claude-code/issues/14160) ("Allow custom instructions
for auto-compact via settings or CLAUDE.md") — strongly suggesting native compaction-prompt
override does not yet ship. **Treat hard-compact's core claim as unverified-to-likely-false
until tested directly against v2.1.241**; the "telegraphic/dense" prompt-design idea itself
is sound and worth reusing even if hard-compact's delivery mechanism doesn't work as
advertised.

Beyond that one repo, genuine "replace what `/compact` does" tools don't exist. What exists
instead: (1) PreCompact hooks injecting extra context for the *unmodifiable* built-in
compactor to fold in (compact-plus, CMV); (2) fully bypassing autocompact via `claude config
set --global autoCompact false` plus a manual save/resume slash-command workflow — the only
verified-real way to fully control what "compaction" means for a session today.

Also useful as secondary bibliography: **ramonsaraiva/rottencontext**, a curated list/site
of context-rot patterns and tools (STATE.md-as-source-of-truth, task-scoped sessions,
repo-packing tools like Repomix/CTX/Gitingest), citing Anthropic's own "Effective Context
Engineering for Agents" and Chroma's "Context Rot" research.

### The single most important unclaimed mechanism: `PostCompact`

Of all 33 current hook events, **`PostCompact` (fires after compaction completes, and CAN
return `additionalContext`) has zero known adopters** — every "recover state after
compaction" tool found in this survey (cozempic, context-recovery hooks generally) instead
fakes it via `SessionStart` + detecting a `compact_boundary` marker in the transcript,
apparently because `PostCompact` is newer than most of these projects. **This is genuine
unclaimed design space**: a hook that fires precisely post-compaction to re-inject dropped
state, rather than reconstructing "did compaction just happen" indirectly, is more direct
and more reliable than what anyone currently ships.

### Second wave of Class B tools (from the broader ecosystem/topic survey)

A second, independent research pass (topic search on GitHub, `awesome-claude-code`,
`claudepluginhub.com`, curated lists) surfaced a substantial additional set of Class B
tools — several corroborating findings from the dedicated in-session hunt above, several new:

- **Ruya-AI/cozempic (375 stars)** — **found independently by two separate research
  passes**, which is itself a signal worth weighing; see the full write-up in the
  transcript-surgery section above. Cross-referenced here because a second pass flagged it,
  unprompted, as "the closest existing analog to what you're designing" — that
  characterization is reinforced by two independent readings of the code, not one agent's
  opinion.
- **alexgreensh/token-optimizer (1,948 stars)** — badges read "survives compaction:
  checkpoint + restore" and "context quality: live score." Explicitly positions itself
  against Headroom/RTK-style tools (which it says cover only 15-25%, command-output
  compression) by additionally targeting bloated config, unused skills, stale memory,
  **compaction loss**, model misrouting, and behavioral waste — i.e. explicitly framed as a
  superset covering our exact problem. Live dashboard (tokens/$/turns), cache-safe measured
  savings, cross-platform (Claude Code, OpenCode, OpenClaw, Codex, Hermes, Copilot). High
  priority for a follow-up close read.
- **GMaN1911/claude-cognitive (449 stars)** — a genuinely different mechanism: a "Context
  Router" does attention-based file injection with HOT (>0.8 relevance -> full injection) /
  WARM (0.25-0.8 -> headers only) / COLD (<0.25 -> evicted) tiers; files decay when
  unmentioned, activate on keyword match, co-activate with related files. Paired with a
  "Pool Coordinator" for multi-instance state sharing. Claims 64-95% token savings,
  validated on a 1M+ line / 3,200-module codebase with 8 concurrent instances. This is an
  LRU/attention-cache approach to *content* within a session (as opposed to whole-session
  compaction) — a distinct angle from every other tool in this survey.
- **Arkya-AI/claude-context-os (295 stars)** — not code, a methodology (CLAUDE.md +
  `/handoff` command + on-demand templates), but well-researched: cites arXiv:2409.10715
  (LLMs reliably track only 5-10 rules before degrading), Chroma's "Context Rot" research
  (topically-related filler is *worse* than random noise), and Anthropic's own Claude 4
  prompt-engineering guidance. Names five specific things default summarization loses —
  precise numbers, conditional logic, decision rationale, cross-document relationships, open
  questions — and designs handoff templates specifically to preserve those five. **Directly
  useful prior art for designing our own checkpoint/handoff document schema.**
- **egorfedorov/claude-context-optimizer** — surfaced independently three times
  (awesome-claude-code, claudepluginhub.com, and topic search), a real traction signal.
  Tracks every file read/edit/search to learn which files are actually reused vs. wasted;
  v4.9 adds **per-tool cost pricing** — learns the *real* measured token cost per MCP tool
  call instead of assuming a flat constant (its own example: discovered one Linear MCP tool
  averaged 38K tokens vs. a guessed 200); ships team-shareable, path-sanitized,
  git-committable "pattern digest" files so a fresh clone doesn't re-learn the same waste.
  Strong reference for a "context economics" feature.
- **serpro69/capy** — "Context-Aware Prompting," architecturally distinct: operates at the
  **MCP protocol layer** so raw tool output (a 56KB API response, 59KB of GitHub issues, a
  45KB log) is processed in an isolated sandboxed subprocess and **never enters the context
  window at all**, rather than being pruned after the fact — the same "prevent at the source"
  philosophy as Context Mode's sandbox lever, arrived at independently. Also ships a
  **Session Vault**: an encrypted, cross-machine-syncable archive/restore/resume of full
  sessions, explicitly because (its own words) "Claude Code deletes sessions after 30 days,
  `/compact` rewrites them in place, and nothing survives across projects or an accidental
  delete." Class A/B hybrid; worth weighing directly against a post-hoc-pruning approach.
- **headroomlabs-ai/headroom** (also listed under a second org/fork, `chopratejas/headroom`
  — two very different star counts, 67,260 vs 45,078, for what claims to be the same
  project; sanity-check before citing either number) — compresses tool outputs/logs/files/
  RAG chunks before the model sees them (60-95% reduction on JSON, 15-20% general),
  **reversible** via a "CCR" cache (originals retrievable on demand), also trims the model's
  *own* verbose output, `headroom learn` mines failed sessions into `CLAUDE.local.md`/
  `AGENTS.md`, wraps 14+ agent CLIs. A real, mature product (PyPI, npm, and a dedicated
  HuggingFace compression model). Class B, worth a direct feature comparison given its
  apparent maturity and adoption.
- **gmickel/flow-next (685 stars)** — defines six named "handover objects" (spec -> task
  graph -> worker run -> reviewed PR -> receipt), each reviewed by a *different* model and
  frozen at handoff; re-anchors each worker with fresh context per task. Cites SlopCodeBench
  (arXiv:2603.24755): across 93 chained checkpoints, no model finished a problem
  end-to-end and pass rate collapsed from 17.2% to 0.5% — worth citing as literature for the
  problem statement (agent quality decays over long chains without structural handoff
  discipline, not just a context-window-size problem).
- **Hainrixz/the-architect (472 stars)** — one-shot "blueprint" generator: interviews the
  user, writes a self-contained markdown blueprint that a *different* Claude Code instance
  with zero prior context can build from unassisted. Clean minimal pattern for "what makes a
  handoff doc genuinely sufficient."
- **ykdojo/claude-code-tips "dx" plugin's `/handoff` skill** — the simplest possible
  reference implementation: one SKILL.md that writes `HANDOFF.md` (Goal / Current Progress /
  What Worked / What Didn't Work / Next Steps) and tells the user to start a fresh session
  pointing at it. Good minimal baseline to contrast against the more elaborate systems above.
- **karanb192/claude-code-hooks (484 stars)** — a 20-plugin hook marketplace (1,570 passing
  tests); not context-management itself, but its `session-logger` hook (SessionStart +
  PostToolUse + SessionEnd, async, file-locked, secret-redacted) is a clean reference for
  hook-wiring hygiene.
- **usernametron/everything-claude-code's `context-budget` skill** — a static/config-bloat
  auditor: "audits Claude Code context window consumption across agents, skills, MCP
  servers, and rules, identifying bloat and redundant components," producing prioritized
  token-savings recommendations. Complementary to *runtime* pruning tools (cozempic,
  token-optimizer) — this targets the fixed per-turn overhead of a user's own configuration.
- **Barnett-Studios/cxpak** — Rust MCP server producing "token-budgeted, annotated context
  bundles" for a task from a typed dependency graph (43 languages, WASM-sandboxed plugin
  SDK, signed releases). Context-packing/budgeting, not compaction — a different point in
  the pipeline (assembling *initial* context efficiently) than most tools here (cleaning up
  context that's already accumulated).
- **JuliusBrussee/Caveman** — token compression via a terser output encoding ("caveman
  speak"), ~65% token reduction claimed.
- **Anthropic's own official marketplace (`anthropics/claude-plugins-official`, 286 entries
  scanned in full for memory/context/compaction/checkpoint/handoff/session/token-budget/
  statusline keywords):** beyond the Class A entries already listed above, two
  first-party plugins sit at the Class B boundary — **session-report** (generates an
  explorable HTML report of tokens, cache efficiency, subagent breakdown, and
  most-expensive-prompts from local transcripts — read-only/post-hoc, but the closest thing
  to an official statement of what Anthropic thinks session diagnostics should surface, and
  worth modeling our own field list on) and **ralph-loop** ("Claude works on the same task
  repeatedly, seeing its previous work, until completion" — a fresh-context-per-iteration
  pattern in the same family as flow-next's per-task re-anchoring). **Bottom line: the
  official marketplace has essentially nothing that manages in-session context/compaction
  quality, tool-result pruning, or checkpointing during a live session** — a meaningful
  confirmation that this space is open even in Anthropic's own curated view of the
  ecosystem.
- **From the three "awesome" curated-list repos:** composio-community/awesome-claude-plugins
  (1,901 stars) — read in full, **24 plugins, zero memory/context/compaction entries**
  despite the generic framing (a small negative-finding data point). quemsah/awesome-claude-plugins
  (1,215 stars) is actually an automated star-count leaderboard, not hand-curated; it
  surfaced one large new name not found elsewhere: **OthmanAdi/planning-with-files
  (26,267 stars)** — "crash-proof markdown plans, session recovery after `/clear` and
  compaction, per-turn re-injection against context rot, deterministic completion gate" — a
  direct hit on our exact problem statement at a very high star count; not cloned/deep-read
  due to time, **flagged as a priority follow-up**. ccplugins/awesome-claude-code-plugins
  (922 stars) was the richest of the three: **NicolasPrimeau/artel** (shared semantic
  memory + task/agent-to-agent messages + **session handoffs across machines and LLM
  providers**, Class A/B hybrid), **soutone/now-next-methodology** (a lightweight two-file
  NOW.md/NEXT.md task-state continuity convention), **WhymustIhaveaname/claude-memory-manager**
  (Class A, global memory tier with a web UI), **WaterTian/cc-hud** (Class B statusline:
  model, context-usage bar, active subagents, rate limits in one compact line). Two
  name-pattern false positives worth noting so they aren't re-investigated:
  `fable-baton` (model-tier routing, not context handoff) and `claude-session-tint`
  (terminal tab coloring only).
- **A saturated commodity layer of statusline context-bar tools** was confirmed across
  multiple independent sources (awesome-claude-code lists `claude-statusbar`, `Claumon`,
  `ccvitals`, `cc-probeline`, `claude-code-status-bar`; topic search surfaces more, e.g.
  threshold-warning tools firing at 120k/150k/180k tokens) — reinforcing the "Ground truth"
  finding above that live context-percentage display is a solved, commodity problem, not
  differentiated territory. Two macOS-native session monitors (`gmr`'s **Claude Status** and
  `sverrirsig`'s **claude-control**) are notable for surfacing **"compacting" as a
  first-class session state** alongside active/waiting/idle — a UX detail (naming
  compaction-in-progress explicitly) worth borrowing regardless of platform.
- **Literature/benchmarks worth citing in our own design doc** (not tools to depend on):
  Anthropic's own engineering blog post "Effective Context Engineering for AI Agents"
  (cited repeatedly across the ecosystem as the canonical source on compaction / just-in-time
  retrieval / note-taking); **AMAP-ML/LongHorizon-Harness** (arXiv:2608.01964, #1 on HF
  Daily Papers) — plan -> act-with-fresh-context -> verify-in-real-environment ->
  checkpoint-or-recover -> repeat, benchmarked on WeaveBench/OSWorld 2.0/Terminal-Bench 2.1;
  SlopCodeBench (arXiv:2603.24755, cited via flow-next above); Chroma's "Context Rot"
  research and arXiv:2409.10715 (cited via claude-context-os above).

### Ecosystem directories surveyed directly

- **hesreallyhim/awesome-claude-code** has a dedicated "Memory & Context Persistence"
  category (158-row resource table) — nearly every tool named in the "second wave" section
  above was cross-listed there; no entries were found that *aren't* already covered
  somewhere in this document.
- **claudepluginhub.com** — direct crawling of the bare domain 403'd (bot-blocked; retried
  once per instructions, still blocked); worked around via `www.` prefix + WebSearch, which
  did return real indexed pages. It's a genuine plugin discovery hub (semantic search,
  tech-stack ranking, and its own marketing claims a "diagnostic tool to check local Claude
  Code plugin setup for broken installs, risky hooks, and **context costs**"). Confirmed
  several tools already named above (claude-context-optimizer, context-mode, the
  `context-budget` skill) plus purely Class A entries (graphiti-context-hub, ai-memory,
  memind-memory, brain-memory — the last notable for an explicit "consolidate a session into
  durable memory before exiting" checkpoint-on-exit pattern, sitting right at the Class A/B
  boundary).
- **Not deep-dived, flagged honestly as a time-boxed gap:** `jeremylongshore/claude-code-plugins-plus-skills`
  (a 471-plugin marketplace) and `athola/claude-night-market` (23 plugins, one described as
  "context optimization") — both surfaced but not opened.

### Reddit / community sentiment — structurally inaccessible, substitute evidence only

**Direct Reddit access is blocked at the tool level** (WebSearch with `allowed_domains:
["reddit.com"]` returns a hard 400, "not accessible to our user agent") — confirmed as a
structural limitation, not a failed/empty query; all generic Reddit-targeted searches
returned zero actual reddit.com URLs, dominated instead by glama.ai MCP mirrors, dev.to
posts, and vendor blogs. Substitute findings:
- Several comparison/roundup articles exist for 2026 (designrevision.com "Best Claude Code
  Plugins (2026)," felo.ai, blog.memoryplugin.com "Claude Code Memory Explained,"
  trigidigital.com "Claude-Mem Plugin Review 2026," plurality.network) — not deep-read,
  flagged as a follow-up if more comparison-article coverage is wanted.
- One unverified synthesized claim surfaced repeatedly in search summaries: "ClaudeMem
  achieves approximately 10x token efficiency compared to manual context management" —
  **[unverified]**, no primary source pinned down.
- General community wisdom: recommendation to run `/compact` proactively around ~60%
  context capacity rather than waiting for it to be forced near 95%, to avoid context rot
  setting in before compaction happens.
- **A tool called "Context Travel MCP" surfaced only in search-result summaries, not as a
  pinned canonical repo** — described as supporting checkpoint/reset-to-checkpoint (with an
  injected handoff message summarizing accomplishments/next-steps), a checkpoint list, and
  context-window health monitoring. This sounds squarely Class B and, if real, would belong
  in this survey — but it could not be independently verified or attributed to a specific
  repository in the time available. **Flagged as an open gap**, not a confirmed finding —
  worth a direct, targeted follow-up search before relying on it.
- The clearest "critical of claude-mem" signal found anywhere is not organic Reddit
  discussion but ReflexioAI/claude-smart's own vendor-reported comparison (see Class A
  section above) — flag accordingly if cited, since it is marketing, not neutral sentiment.

---

## Mechanism patterns observed

| Technique | Who uses it | Does it work? |
|---|---|---|
| PreCompact -> background LLM extraction -> markdown daily log | coleam00/claude-memory-compiler | Yes — real, working, <10s budget respected |
| PreCompact -> deterministic snapshot (no LLM) -> SessionStart(compact) re-injection | mksglu/context-mode | Yes — zero API cost, docs-sanctioned pattern |
| PreCompact -> proxy via git log/diff instead of transcript_path | julep-ai/memory-store-plugin | No — broken; also based on a false premise (transcript IS accessible via `transcript_path`) |
| Hook -> file queue -> "proactive" skill drains queue and calls MCP tool | julep-ai/memory-store-plugin | No — weak guarantee, 4 failed iterations documented in their own CHANGELOG |
| Pure MCP tools, agent must proactively call save/restore | mkreyman/mcp-memory-keeper | Partially — works if invoked, but README admits it needs a human/CLAUDE.md nudge, not automatic |
| Forked-subagent skill for retrieval (keep main context clean) | zilliztech/memsearch | Yes — sound context-economy architecture |
| MCP tool schema footprint reduction via profile flag | mkreyman/mcp-memory-keeper | Yes, reactive fix after users complained (issue-driven) |
| PreToolUse/MCP-layer sandbox: divert raw tool output to disk+index at creation time, return small result | mksglu/context-mode; serpro69/capy (independently, at MCP-protocol layer) | Yes — largest reduction numbers in the survey; two independent implementations converge on "prevent at source" over "clean up after" |
| Fork-never-mutate transcript surgery + `claude --resume` on a new session ID | Mor-Li/sculptor, zhurong666/cc-session, CosmoNaught/claude-code-cmv, Ruya-AI/cozempic, waterside0219/session-forge | Yes, with strict caveats — real and working when tool_use/tool_result pairs are ID-matched, parentUuid re-stitched, and thinking blocks/write-tool-inputs are left alone; CMV's issue #11 shows what breaks (write-tool-input stubbing corrupted real files) when the caveats are ignored |
| Guard daemon polling context% with tiered auto-prune-and-reload + safe-point protection | Ruya-AI/cozempic | Yes — found independently by two research passes; safe-point logic (never reload mid-tool-call/mid-subagent) is the key correctness feature |
| `PostToolUse` hook returning `updatedToolOutput` to replace what the model sees | PCIRCLE-AI/toonify-mcp (demonstrates the API) | Yes — confirmed real Claude-Code-specific capability (Codex CLI's hooks can't do this) |
| Age-tiered tool-result trimming (recent verbatim, mid-age minified, old stubbed) + always keep full original on disk, reversibly | ZizzX/claude-output-trim, Ruya-AI/cozempic | Yes — "truncation is reversible, not destructive" framing recurs across implementations and is worth adopting as a hard design rule |
| Gate/block manual `/compact` until unsaved-work is checkpointed (never block auto-compact) | sofumel/claude-handoff-revive | Yes — sound because a PreCompact hook has no model access ("no inference turn at compaction time"), so this is the correct place to force a save that already happened on `Stop`, not to attempt one inline |
| Fully disable native autocompact (`claude config set --global autoCompact false`) + manual save/resume slash commands | EliaAlberti/cpr-compress-preserve-resume | Yes — verified real, current escape hatch; corroborated independently by a second repo's references to `DISABLE_AUTO_COMPACT` |
| Claimed native override of the compaction summarizer's prompt via a `compactSummaryPrompt` settings key | Oldrich333/hard-compact | **No / unverified-to-false** — absent from official settings docs; two *open* Anthropic feature requests ask for exactly this capability, implying it doesn't ship yet |
| `PostCompact` hook (fires after compaction, can return `additionalContext`) | **Nobody** — zero adopters found across the entire survey | N/A — it is the docs-sanctioned direct mechanism for post-compaction re-injection, yet every "recover after compaction" tool instead fakes it via `SessionStart` + detecting a `compact_boundary` marker, apparently because it's newer than most of these projects. **Open design space.** |
| Attention-tiered file injection (HOT/WARM/COLD relevance decay + co-activation) as an alternative to whole-session compaction | GMaN1911/claude-cognitive | Plausible, unusually validated (1M+ line codebase, 8 concurrent instances) but a single-source claim — a genuinely different angle (managing *content* relevance) worth understanding even if we don't adopt it wholesale |
| Static config/skill/MCP-schema bloat auditing (distinct from runtime pruning) | usernametron/everything-claude-code `context-budget` skill, mkreyman/mcp-memory-keeper's `TOOL_PROFILE` | Yes — real, complementary to runtime pruning; addresses the fixed per-turn tax of a user's *own* configuration rather than transient tool-result bloat |
| Native statusline `context_window.used_percentage`/`remaining_percentage` | Anthropic (built-in) | Yes — already solved, no plugin needed; ccusage/ccstatusline/cc-hud etc. are convenience wrappers around this native field, not new capability |

---

## Design implications for our plugin

**On the overall landscape:** Class A (cross-session memory) is crowded and largely
solved-if-imperfect — dozens of tools, one dominant player (claude-mem) with real, serious,
documented problems (security, cost, storage bloat) that a new entrant could differentiate
against but that we are explicitly not trying to compete with. **Class B (in-session
management) is comparatively open but not empty** — Context Mode (~20k stars, months old)
and cozempic (found independently twice, smaller but unusually well-matched to our exact
brief) both validate strong demand and both hand us proven, working mechanisms to build on
or deliberately differentiate from, and a "crowded niche" of ~25+ handoff/checkpoint/
context-guard repos confirms the pain is widely felt even where no single tool has won yet.

**Mechanisms to adopt or strongly consider:**
- **`PreCompact` -> snapshot -> `PostCompact` (or `SessionStart` with `source: compact`) ->
  re-inject `additionalContext`** is the docs-sanctioned pattern for lossless-compaction
  checkpointing, and **`PostCompact` specifically is unclaimed** — every surveyed tool fakes
  post-compaction detection via `SessionStart` + a transcript marker instead of using the
  direct hook. Using `PostCompact` properly is a concrete, low-risk differentiator.
- **Prefer deterministic, non-LLM snapshotting where possible** (Context Mode's approach —
  a "table of contents with rehydration queries," not a summary) over LLM-summarized
  checkpoints (claude-memory-compiler, memsearch) — cheaper, faster, no recursion guard
  needed, and avoids adding a new class of API cost/failure mode to the exact problem
  (context/cost bloat) we're trying to solve.
- **Prevent bloat at the source, not just clean it up after** — Context Mode's `PreToolUse`
  sandbox and capy's MCP-layer sandbox independently converged on diverting large tool
  output to disk+index *before* it ever reaches the context window, returning only a small
  computed result. This is more effective (largest reduction numbers in the survey) than
  after-the-fact trimming.
- **If we touch the raw session JSONL for transcript surgery, follow the convergent safety
  rules exactly** (see Class B > Transcript surgery section): fork to a new file + new
  session ID, never mutate in place; pair `tool_use`/`tool_result` by ID; re-stitch
  `parentUuid` after any drop; never touch `thinking` blocks except by deleting their whole
  turn; **never stub/replace `tool_use.input` for Write/Edit/MultiEdit/NotebookEdit** (this
  caused real, filed data loss in CosmoNaught/claude-code-cmv); guard against the
  live-rewrite-race (don't reload while a tool call/subagent is in flight — cozempic's
  "safe-point" pattern).
- **Frame any trimming/pruning as reversible, never destructive**: keep the full original on
  disk and tell the model exactly how to retrieve it (ZizzX/claude-output-trim's pattern) —
  this defuses the main objection to any output-pruning feature (loss of detail) at low
  cost.
- **Prefer hooks over a large persistent MCP server for anything that must be automatic** —
  every surveyed pure-MCP-tool design (mcp-memory-keeper, julep's bridge) degrades to "hope
  the agent remembers to call the tool," which its own maintainers admit is not a real
  guarantee. If we do add MCP tools, keep the schema count minimal (every tool schema is a
  standing per-turn context tax — mcp-memory-keeper's own `TOOL_PROFILE` flag is a direct,
  issue-driven admission of this).
- **Use `PreCompact`'s `transcript_path` directly**; don't proxy "what happened this
  session" via git log/diff the way julep's broken implementation does, and don't assume
  the transcript is inaccessible to a PreCompact hook (it isn't — this specific false
  assumption broke a real shipped tool).
- **A PreCompact hook has no model access** ("no inference turn at compaction time," per
  sofumel/claude-handoff-revive's own design notes) — any "smart" state-saving must already
  have happened earlier (e.g. on `Stop`) or be handled by gating/blocking compaction until a
  save has occurred, not by trying to compute a summary inline inside PreCompact.
- **Independently re-verify, don't assume, two specific upstream behaviors before depending
  on them**: (1) whether `PreCompact` reliably fires for *manual* `/compact` on our exact CLI
  version — a filed upstream issue (#13572) claiming it sometimes doesn't was closed "not
  planned"; (2) whether a native compaction-prompt-override setting exists — the one tool
  claiming this (hard-compact's `compactSummaryPrompt`) could not be verified and two open
  Anthropic feature requests suggest it doesn't ship yet.
- **Respect hook latency budgets and argument-size limits**: do slow/LLM work in a detached,
  non-blocking background process (claude-memory-compiler's "<10s" pattern), not inline in
  the hook; watch for `MAX_ARG_STRLEN`-class failures when passing large transcript content
  as a command-line argument (memsearch hit this at >128KB).
- **Don't duplicate the native statusline context-percentage feature** (`context_window.
  used_percentage`/`remaining_percentage` are already provided) — if we surface context
  state, build on top of the native field or read the transcript's `compact_boundary`
  markers for compaction telemetry (ccstatusline's approach), don't re-derive it from
  scratch.
- **Design the checkpoint/handoff document schema around what's actually known to get lost**
  in default summarization — precise numbers, conditional logic, decision rationale,
  cross-document relationships, and open questions (per claude-context-os's research-backed
  framing) — rather than a generic "summary."
- **Watch for the specific operational failure modes that recur across nearly every tool
  surveyed**: orphaned background daemons/watchers (memsearch #692, claude-mem's observer
  sessions), hooks or MCP server processes left running after uninstall (Context Mode
  #1058), CWD-sensitive storage paths (mcp-memory-keeper #31), silent `|| true` failure
  swallowing masking broken functionality (julep), and unauthenticated local HTTP APIs /
  unsanitized content injected into `additionalContext` creating a prompt-injection surface
  (claude-mem's audited findings) — sanitize/escape anything from tool output or file
  content before it goes into `additionalContext`, and treat popularity/marketing claims
  (star counts, "N% reduction," download counts) with the same skepticism this survey
  repeatedly had to apply.

---

## Open questions / gaps

- **"Context Travel MCP"** surfaced only in web-search summaries (checkpoint/reset-to-
  checkpoint with handoff messages, context-health monitoring) but no canonical repository
  was pinned down — worth a direct, targeted follow-up search; if real, it belongs in the
  Class B section above.
- **`anthropics/claude-code#13572`** ("PreCompact hook not triggered when /compact command
  runs," closed as not planned) should be independently re-tested against our exact local
  CLI version (v2.1.241) before our design depends on PreCompact firing reliably for manual
  compaction.
- **claude-mem's star-count trajectory could not be fully audited** — the diagnostic
  stargazer-timestamp-burst test was blocked by API access in our environment (404/401 on
  both authenticated and unauthenticated attempts); the growth curve looks organic-shaped
  but this is not a clean bill of health, and the tool's own promotion of a crypto token is
  an unresolved motive-level red flag worth remembering when citing its popularity.
- **`headroomlabs-ai/headroom` vs `chopratejas/headroom`** show materially different star
  counts (67,260 vs 45,078) for what claims to be the same project — needs a sanity check
  (fork? duplicate listing? diverged projects?) before citing either number externally.
- **Two plugin marketplaces were found but not opened** due to time-boxing:
  `jeremylongshore/claude-code-plugins-plus-skills` (471 plugins) and
  `athola/claude-night-market` (23 plugins, one described as "context optimization") — worth
  a follow-up pass if we want full marketplace coverage.
- **`OthmanAdi/planning-with-files`** (26,267 stars, "session recovery after `/clear` and
  compaction, per-turn re-injection against context rot") is a very high-star direct hit on
  our problem statement that was found only via a leaderboard listing and never cloned or
  code-read — this is the single highest-priority follow-up clone from this entire survey
  before finalizing our own design, since it may already occupy the exact space we're aiming
  for.
- **Reddit is structurally inaccessible to our search tooling** (hard domain block) — any
  community-sentiment conclusions in this report lean on blog posts and repo issue trackers
  instead; genuine organic user sentiment (as opposed to vendor comparison claims) is
  under-sampled here.
- **claude-mem's outbound telemetry was not independently traced end-to-end** — its own code
  comments claim a scrubbed, counts-only payload, but this was not verified at the network
  layer.
- Several very-high-star repos surfaced by raw topic search (e.g. "ponytail" at 100k+
  stars, "i-have-adhd" at 23k+) were sanity-skimmed as clearly irrelevant to memory/context
  and excluded — flagged here only so it's clear they were seen and deliberately excluded,
  not missed.

---

## Sources

**Primary tools (Class A):**
- https://github.com/thedotmack/claude-mem
- https://github.com/zilliztech/memsearch
- https://milvus.io/blog/adding-persistent-memory-to-claude-code-with-the-lightweight-memsearch-plugin.md
- https://milvus.io/blog/claude-code-memory-memsearch.md
- https://github.com/julep-ai/memory-store-plugin
- https://github.com/mkreyman/mcp-memory-keeper
- https://github.com/coleam00/claude-memory-compiler
- https://github.com/lucasrosati/claude-code-memory-setup
- https://github.com/obra/claude-memory-extractor
- https://github.com/raiyanyahya/recall
- https://github.com/Durafen/Claude-code-memory
- https://github.com/agenticnotetaking/arscontexta
- https://github.com/rohitg00/pro-workflow
- https://github.com/activeloopai/hivemind
- https://github.com/nagisanzenin/engram
- https://github.com/tigerless-labs/autoharness
- https://github.com/SethGammon/Citadel
- https://github.com/ReflexioAI/claude-smart
- https://github.com/kevin-hs-sohn/hipocampus

**Primary tools (Class B):**
- https://github.com/mksglu/context-mode
- https://www.mindstudio.ai/blog/claudemem-vs-context-mode-claude-code-memory-plugins
- https://www.mindstudio.ai/blog/context-mode-claude-code-315kb-to-5kb-session-compression
- https://github.com/Mor-Li/sculptor (arXiv:2508.04664)
- https://github.com/zhurong666/claude-code-session-editor
- https://github.com/CosmoNaught/claude-code-cmv
- https://github.com/Ruya-AI/cozempic
- https://github.com/waterside0219/session-forge
- https://github.com/meridianix/clawdbot-session-pruner
- https://github.com/javimosch/claude-session-optimizer
- https://github.com/justinritchie/cowork-session-recover
- https://github.com/mason0510/fix-jsonl
- https://github.com/mcpware/claude-code-organizer
- https://github.com/chetools/ChatJsonEditor
- https://github.com/didvc/claude-code-jsonl-editor
- https://github.com/ArkNill/claude-code-hidden-problem-analysis
- https://github.com/sofumel/claude-handoff-revive
- https://github.com/u-ichi/compact-plus
- https://github.com/EliaAlberti/cpr-compress-preserve-resume
- https://github.com/Ricky-Stevens/context-guardian
- https://github.com/Guard8-ai/ContextGuard
- https://github.com/ryoppippi/ccusage (ccusage)
- https://github.com/sirmalloc/ccstatusline
- https://github.com/ZizzX/claude-output-trim
- https://github.com/PCIRCLE-AI/toonify-mcp
- https://github.com/arbiterForge/codeArbiter
- https://github.com/Oldrich333/hard-compact
- https://github.com/ramonsaraiva/rottencontext
- https://github.com/anthropics/claude-code/issues/13572
- https://github.com/anthropics/claude-code/issues/55905
- https://github.com/anthropics/claude-code/issues/14160
- https://github.com/alexgreensh/token-optimizer
- https://github.com/GMaN1911/claude-cognitive
- https://github.com/Arkya-AI/claude-context-os
- https://github.com/egorfedorov/claude-context-optimizer
- https://github.com/serpro69/capy
- https://github.com/headroomlabs-ai/headroom
- https://github.com/gmickel/flow-next
- https://github.com/Hainrixz/the-architect
- https://github.com/ykdojo/claude-code-tips
- https://github.com/karanb192/claude-code-hooks
- https://github.com/usernametron/everything-claude-code
- https://github.com/Barnett-Studios/cxpak
- https://github.com/JuliusBrussee/Caveman
- https://github.com/OthmanAdi/planning-with-files
- https://github.com/NicolasPrimeau/artel
- https://github.com/soutone/now-next-methodology
- https://github.com/WhymustIhaveaname/claude-memory-manager
- https://github.com/WaterTian/cc-hud
- https://github.com/mirkobozzetto/espresso

**Ecosystem directories / marketplaces:**
- https://github.com/anthropics/claude-plugins-official
- https://github.com/hesreallyhim/awesome-claude-code
- https://claudepluginhub.com
- https://github.com/composio-community/awesome-claude-plugins
- https://github.com/quemsah/awesome-claude-plugins
- https://github.com/ccplugins/awesome-claude-code-plugins

**Literature cited by surveyed tools (secondary sources, not independently verified by us):**
- Anthropic, "Effective Context Engineering for AI Agents" (engineering blog)
- arXiv:2409.10715 (LLM rule-tracking degradation, cited by claude-context-os)
- arXiv:2603.24755, SlopCodeBench (cited by flow-next)
- arXiv:2608.01964, LongHorizon-Harness (AMAP-ML)
- Chroma, "Context Rot" research (cited by claude-context-os, rottencontext)
- gist.github.com/roman-rr/0569fc487cc620f54a70c90ab50d32e3 ("MemPalace" fake-star case
  study — unrelated to claude-mem, cited only as a comparison case for what fake-star
  evidence looks like)

**Claude Code documentation (fetched live, 2026-08-23):**
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/plugins
- https://code.claude.com/docs/en/plugins-reference
- https://code.claude.com/docs/en/statusline
