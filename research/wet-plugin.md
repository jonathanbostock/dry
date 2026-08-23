# "Wet" — Research Report

Target: a Claude Code context/short-term-memory tool called "Wet" (or WET as a pun/acronym), mentioned by Jonathan. Found on the first search angle.

## Summary

**Found.** `wet` = **"Wringing Excess Tokens"** ("Wet Claude" / "wet-compress"), by Nick Oak (GitHub: `buildoak`, blog: nickoak.com). Repo: **https://github.com/buildoak/wet** — MIT license, Go, 39 stars, 6 forks, first release 2026-03-13 (v0.1.0), latest commit 2026-05-30. It is **not** a Claude Code "plugin" in the formal marketplace/`.claude-plugin` sense — there is no `marketplace.json` or `plugin.json` anywhere in the repo, and it does not appear in `anthropics/claude-plugins-official`. It is a **standalone Go CLI + reverse HTTP proxy**, distributed as a single ~9 MB binary, paired with a Claude Code **Skill** (`wet-compress`, installed to `~/.claude/skills/wet-compress` via `wet install-skill`) and an optional statusline script.

Mechanically: `wet claude [args...]` launches `claude` as a child process with `ANTHROPIC_BASE_URL` pointed at a local proxy (`127.0.0.1:<random free port>`). The proxy sits in front of `api.anthropic.com`, intercepts every `POST /v1/messages`, parses the message array, scores each `tool_result` block for staleness (by "turns since produced"), deterministically compresses/truncates certain tool families in place before forwarding, and exposes a control-plane HTTP API (`/_wet/status`, `/_wet/inspect`, `/_wet/compress`, `/_wet/pause`, etc.) that the Skill drives via `curl`-like CLI calls so Claude itself can decide what to rewrite (a Sonnet subagent does the actual rewriting for agent returns, search results, and file reads — the cases mechanical regex compression can't handle safely).

The single most important finding for our own plugin design: **wet exists only because, at the time it was built (Claude Code ~2.1.7x), there was no hook that could rewrite a *built-in* tool's output before it entered context** (only `updatedMCPToolOutput` existed, MCP-only). wet's author filed `anthropics/claude-code#32105` asking for this. Claude Code **v2.1.121** (shipped ~2026-04-28, per the official CHANGELOG) added exactly that: `PostToolUse` hooks can now return `hookSpecificOutput.updatedToolOutput` for **all** tools, not just MCP. The GitHub issue was subsequently closed as `COMPLETED`. wet's own codebase, as of its last commit (2026-05-30, a month after the fix shipped), has **zero references** to `updatedToolOutput` or `PostToolUse` anywhere — it never migrated off the reverse-proxy architecture. On the locally-installed Claude Code v2.1.241 (~120 patch releases past 2.1.121), wet's core interception mechanism is now solvable natively, in-process, without a MITM proxy. This is a major design cue for us (see Design implications).

## Search log (angles tried)

1. `gh search repos wet claude` — hit on first try: `buildoak/wet`.
2. `gh search repos "claude code" wet` — same result, confirms uniqueness of match.
3. `gh repo view buildoak/wet --json ...` — metadata (stars, license, dates, language).
4. Shallow clone (`git clone --depth 50 https://github.com/buildoak/wet.git`) into `/tmp/research/wet`, full source read.
5. `gh repo view buildoak/tg-agents-wrapper` + `gh api users/buildoak` — confirmed same author has a second project ("wet proxy integration") and a public dev profile (ex-quant/1inch/Revolut/AI-research bio).
6. WebSearch: `buildoak wet claude code proxy "wringing excess tokens"` — surfaced the author's own design-rationale blog post.
7. WebFetch of `https://www.nickoak.com/posts/wet-claude/` — author's first-person account of why/how it was built.
8. WebSearch: `"wet" claude code plugin context compression proxy github` — surfaced wet plus the adjacent ecosystem (claude-rolling-context, magic-compact, tamp, ClaudeSlim — not dissected further per instructions, another agent covers these).
9. `gh issue list -R buildoak/wet` + `gh issue view 7` (open security issue) — maturity/issue-tracker check.
10. `gh issue view 32105 -R anthropics/claude-code --comments` — the upstream feature request wet's design depends on; found it was closed COMPLETED with a rich comment thread (redaction/security use cases, competing designs, PoC evidence).
11. `gh api graphql` on PRs — only 1 external PR merged (Docker serve mode), otherwise solo-maintained.
12. `gh search code "wet" --repo anthropics/claude-plugins-official` + directory listing — confirmed absence from the official plugin marketplace.
13. WebSearch `"wet claude" OR "wet-compress" site:reddit.com OR site:news.ycombinator.com` — no discussion found.
14. WebSearch `"wet" "Wringing Excess Tokens" twitter OR x.com` — no discussion found (query got swamped by crypto-token noise).
15. `gh api repos/buildoak/wet/forks` / `branches` — checked whether any fork migrated to native `PostToolUse` hooks post-v2.1.121; none had.
16. Fetched `anthropics/claude-code` `CHANGELOG.md` via `gh api` and grepped for `updatedToolOutput`/`PostToolUse` — confirmed the v2.1.121 entry, dated the fix, and confirmed wet's source has no matching reference.

Total: 16 distinct angles across GitHub search/API, web search, and direct source reading — well past the 8-10 threshold, though the target was located on angle 1.

## Findings

- **Repo:** [buildoak/wet](https://github.com/buildoak/wet) — "wet claude. Wringing Excess Tokens - transparent API proxy that compresses stale tool results in Claude Code sessions." MIT license (`Copyright (c) 2026-present Nick Okoneshnikov`). 39 stars, 6 forks, primary language Go, ~13.7k KB repo, `pushedAt: 2026-05-29T20:05:26Z`.
- **Author:** Nick Oak (`buildoak`), bio "Builder; prev: quant, 1inch, Revolut, AI research (IITP RAS), MIPT". Also maintains `buildoak/tg-agents-wrapper` (a Telegram bot wrapper for Claude Code/Codex that integrates the wet proxy).
- **Author's own writeup:** [Wet Claude: Let Claude Optimize Its Own Context](https://www.nickoak.com/posts/wet-claude/) — describes the "meta-compression" framing (Claude optimizing Claude's own context), the pivot from pure deterministic compression to giving Claude the driver's seat, and reports going from 140k→100k tokens and turn-150→turn-300 session sustainability in his own dogfooding.
- **Distribution:** Homebrew tap (`brew tap buildoak/tap && brew install wet`), `go build` from source, or Docker (`docker build -t wet-proxy .`). Explicitly **not** installed via Claude Code's `/plugin` marketplace mechanism — confirmed absent from `anthropics/claude-plugins-official` (checked both its `plugins/` and `external_plugins/` listing and via `gh search code`).
- **Upstream dependency / origin story:** [anthropics/claude-code#32105](https://github.com/anthropics/claude-code/issues/32105) — buildoak's own feature request, "PostToolUse hooks: allow `updatedToolOutput` for built-in tools." Audited sessions show tool results are ~49–73.6% of context; Bash output size is unpredictable from the command string alone (`git status` ranged 5–5,491 tokens in his sample, a 1098x spread), so `PreToolUse` can't gate it. The issue is rich with adjacent projects that surfaced in the same thread: `MaxwellCalkin/sentinel-ai` (PII/secret redaction hook), `Dave-London/Pare` (MCP servers that emit pre-structured low-token output instead of raw CLI text), `Digital-Process-Tools/claude-remember` (Haiku-summarized *between-session* memory, claims 81% reduction), and a mention that `rtk-ai/rtk` (a third-party PreToolUse rewriter) had a security bug where it returns `permissionDecision: "allow"` on every rewritten command, bypassing Claude Code's permission system (`rtk-ai/rtk#260`) — worth avoiding that failure mode ourselves.
- **Resolution:** The issue was closed `COMPLETED` on 2026-04-24... but the actual shipping evidence is in Claude Code's own changelog, not the issue thread: **v2.1.121** added "PostToolUse hooks can now replace tool output for all tools via `hookSpecificOutput.updatedToolOutput` (previously MCP-only)." wet's source was never updated to use it (verified via `grep -rn "updatedToolOutput\|PostToolUse"` — zero hits in the whole repo).
- **Known open issues** (buildoak/wet, all unresolved as of research date): `#7` Session data files world-readable (`0o755`/`0o644` perms on `~/.wet/sessions/*/session.jsonl`, `replacements.json`, `stats-*.json`, `wet.log` — readable by any local user, remediation would be `0700`/`0600`); `#1` static CC-version compatibility diagnostics; `#3` startup warnings for CC-version/Claude.app mismatches; `#4` E2E agentic test pipeline for CC version validation; `#5` cron watchdog for CC version monitoring. Several of these (`#1`, `#3`, `#4`, `#5`) reflect the underlying fragility of a proxy that depends on Claude Code's exact HTTP/IPC behavior not changing between releases.
- **No social-media or forum footprint found.** Targeted searches on Reddit, Hacker News, and X/Twitter for "wet claude", "wet-compress", and "Wringing Excess Tokens" returned nothing relevant — this looks like a genuinely early/niche project (39 stars) rather than one with organic buzz, despite being well-engineered and well-documented.

## Mechanics

### Architecture

```
Claude Code (unmodified) --ANTHROPIC_BASE_URL--> wet proxy (Go, localhost) --> api.anthropic.com
                                                       |
                                          Control plane (/_wet/* HTTP, unauthenticated)
                                          Persistence (~/.wet/sessions/{uuid}/session.jsonl)
```

`wet claude [args...]` (`cli/shim.go`):
- Picks a free TCP port, starts the proxy server in-process, then `exec.Command("claude", args...)` as a child with `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`, `WET_PORT=<port>`, `WET_SESSION_UUID=<uuid>` injected into its env. stdio is passed through directly; SIGINT/SIGTERM are forwarded to the child.
- Session UUID comes from `--resume <uuid>` if present, else a fresh v4 UUID — this is the persistence key.

Proxy (`proxy/proxy.go`, 861 lines, the core file):
- Built on `httputil.ReverseProxy`. `Director` swaps host/scheme to the upstream (default `https://api.anthropic.com`) and strips `Accept-Encoding` so the SSE interceptor can scan plaintext.
- Only `POST` to a path matching `isMessagesPath` (`/v1/messages`, `/messages`, or anything ending in `/v1/messages`) goes through `handleMessagesWithCompression`; everything else is bare-forwarded.
- Per request: reads and JSON-parses the body (`messages.ParseRequest`), computes a `systemHash` fingerprint of the system prompt (with the volatile `x-anthropic-billing-header` line stripped first) to distinguish the **main session** from **subagent** calls (first request through the proxy = main; different system-prompt fingerprint = subagent). Only main-session state is persisted/accounted, so subagents can't corrupt the main session's tracked tool results.
- Re-applies any previously-persisted compressions on every request (important for `--resume`), then re-classifies staleness so `/inspect` reflects the true post-persistence state.
- Runs `messages.ClassifyStaleness` (see below) to get `[]ToolResultInfo`, then either the automatic pipeline (`mode = "auto"`) or a queued selective-compress pipeline (populated by the skill's `/compress` calls) or both.
- Captures **exact** token usage from the real API response: an `sseInterceptor` (SSE) or `jsonUsageInterceptor` (non-streaming pre-flight — added in v0.1.5 once Claude Code started sending those) parses `message_start`/`message_delta` events for `input_tokens`/`output_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens`. This is explicitly used as "ground truth" over any local character-based estimate.
- Writes `~/.wet/stats-{port}.json` after every request (statusline feed) and appends one JSON line per turn to `~/.wet/sessions/{uuid}/session.jsonl`.

### Staleness classification (`messages/staleness.go`)

- Walks the message array; increments a `turn` counter on every assistant message; builds a map of `tool_use_id -> {ToolName, Command, FilePath, Turn}` from assistant `tool_use` blocks, then for each `tool_result` in user messages, looks up its originating tool call and computes `Stale = (currentTurn - originTurn) >= staleAfter`, where `staleAfter` defaults to a global `threshold` (config default: 2) but can be overridden per tool-family via `config.RuleConfig.StaleAfter` (e.g. `git_status` goes stale in 2 turns, `pytest` in 1).
- Token estimate used throughout for pre-compression sizing: `EstimateTokens(s) = len(s) * 10 / 33` (i.e., chars/3.3) — explicitly called out as an approximation used only pre-request; post-request savings always come from the real API usage numbers instead.
- `ExtractToolFamily` pattern-matches the Bash `command` string (prefix checks) into families: `git_status`, `git_log`, `git_diff`, `npm`, `cargo`, `pip`, `docker`, `ls_find`, `make`, `pytest`, else `bash_generic`; non-Bash tools map to `read`/`grep`/`glob`/`unknown`.

### Compression pipeline (`pipeline/pipeline.go`, `pipeline/bypass.go`, `pipeline/tombstone.go`)

`CompressRequest` iterates every classified tool result and applies, in order:
1. Skip if not stale.
2. `ShouldBypass` — hard-skip if: not stale; `is_error && cfg.Bypass.PreserveErrors` (default true); already a tombstone (idempotency, detected by string-prefix `"[compressed: "`); contains image blocks; below `min(cfg.Compression.MinTokens, cfg.Bypass.MinTokens)` (100 tokens default); matches a regex in `cfg.Bypass.ContentPatterns` (defaults: `error`, `exception`, `traceback`, `failed`).
3. Skip `Agent`/`Task` tool results in **auto** mode entirely — the code comment is explicit: *"Agent/Task tool results cannot be adequately compressed by Tier 1 mechanical compression. They require explicit replacement text provided via the control plane... Skip them in auto mode to avoid lossy summarization."* This is why the skill (Tier 2) exists.
4. Skip if the per-family rule's `Strategy == "none"`.
5. Run `compressor.Compress(toolName, command, content)` (Tier 1, deterministic — regex/structural extraction per family, described in the README/architecture doc with per-family compression-ratio tables, e.g. git status 88%, pytest 96%, cargo 94%; calibrated on "13,881 real tool outputs" from SWE-bench).
6. Reject the compression if the compressed token estimate isn't strictly smaller than the original, or if a synthesized **tombstone** (wrapper text) would still be ≥60% of the original token count ("near-pass-through, not worth the replacement").
7. On success, replaces the `tool_result` content in-place in the parsed message array (`ReplaceToolResultContent`, handles both string and content-block-array tool_result shapes) with a tombstone of the form:
   `[compressed: {family} | {summary} | turn {originalTurn}/{currentTurn} | {originalTokens}->{compressedTokens} tokens]`

Tombstones double as the idempotency marker (`IsTombstone` checks the `"[compressed: "` prefix) so a block is never compressed twice.

### Tier 2 — the "meta" / Claude-driven path

This is the part actually named in the pitch ("let Claude optimize its own context"), and it is **not** an automatic proxy-side LLM call by default. There are two distinct code paths worth separating:

- `compressor/tier2.go` exists (calls `api.anthropic.com/v1/messages` directly with the model `claude-sonnet-4-6-20250514`, a hardcoded extraction prompt, 2s timeout, needs its own `ANTHROPIC_API_KEY`) but is **disabled by default** (`config.Default().Compression.Tier2.Enabled = false`) and, per the architecture doc, is superseded by the skill path — this looks like an earlier/experimental automatic-LLM-compression code path that was left in place but turned off.
- The actual shipped Tier 2 mechanism is the **`wet-compress` Skill** (`skill/SKILL.md`, 299 lines) driving `wet` CLI subcommands from **subagents that inherit the session's own context/budget** — i.e., no side-channel API key or billing; it rides on the normal Claude Code subagent mechanism. The skill is explicitly the "manual" to the proxy's "toolbox": *"The proxy is the toolbox - the skill is the manual that makes Claude a self-optimizing agent."*

### The Skill — `wet-compress` (seven phases, strict order)

Frontmatter: `tools: [Bash, Agent]`, version `0.9.0`, trigger phrases like "context heavy," "compress context," "token pressure." Also carries a note about **"Hermes"** compatibility (a second/internal agent name referenced in the skill — telling that agent not to assume wet is present and to prefer native context management unless wet is confirmed active).

- **Phase 0 (mandatory, main session) — Session identity verification.** Runs `wet session salt` (prints a `WET_SALT_...` token embedded into the transcript), then `wet session find <salt>` to confirm the wet proxy is actually attached to *this* session's JSONL. Rationale given verbatim: *"This caused a misdiagnosis (273k reported for a 92k session) on 2026-03-19."* — i.e., a real multi-session cross-talk bug that got hard-coded into the skill as a mandatory gate.
- **Phase 1 — Health check.** `wet status --json`; buckets fill% into light(<10%)/accruing(10-30%)/growing(30-60%)/heavy(>60%) and recommends action accordingly.
- **Phase 2 — Profile.** Spawns a Sonnet 4.6 subagent that calls `wet status --json` (ground truth fill%) and `wet inspect --json` (compressible items only — explicitly NOT the same denominator as fill%), classifies every item via an ordered rule list (ALREADY_COMPRESSED → PROTECTED → BOOT_READ → MECHANICAL → AGENT_RETURN → SEARCH → FILE_READ → EDIT), and returns a fixed-format token budget table, not prose.
- **Phase 3 — Approval gate.** Renders an ASCII plan/dashboard and **hard-stops for explicit user y/n/edit** — "STOP. WAIT FOR USER. DO NOT AUTO-APPROVE" is in the skill file in bold block letters. This is a deliberate design choice distinguishing it from silent auto-compact.
- **Phase 3.5 — Batching.** If 15+ LLM-rewrite items, splits into sequential (not parallel — "parallel subagents compete for the same inspect/compress endpoints") batches of 10-12, ordered by token count descending.
- **Phase 4 — Compress.** A fresh subagent executes: mechanical IDs via `wet compress --ids ...` (no text needed, Tier 1 handles it); LLM-rewrite IDs get hard token budgets (**agent returns ≤150 tokens, search results ≤100, file summaries ≤100**) and an explicit "ANTI-PASS-THROUGH RULE" instructing the subagent not to just wrap/copy the original text, with a worked before/after example (2000 tokens → 40 tokens). Submits via `wet compress --ids id5,id7 --text '{"id5": "...", "id7": "..."}'` (or `--text-file` for large payloads). Compressions are **queued**, applied on the *next* API request, not immediately.
- **Phase 5 — Verify.** Re-runs `wet status --json`, reports new fill% and `session_api_tokens_saved` (the exact, API-derived figure) vs. the estimate.

Hard rules enumerated at the end of the skill (12 total) include: never compress boot reads (`SOUL.md`/`IDENTITY.md`/`USER.md`/`MEMORY.md` — called "SACRED"), never compress file reads without explicit opt-in, never compress errors or the last 3 turns, never auto-approve, never send Grep/Glob output through Tier-1 mechanical compression (head/tail truncation silently drops matches from the middle), never skip Phase 0.

### Storage / state

- `~/.wet/wet.toml` — optional config (`[server].mode`, `[staleness].*_turns`, `[tier2].enabled`, per-family `[rules.*]`).
- `~/.wet/stats-{port}.json` — live stats consumed by the statusline script, rewritten every request.
- `~/.wet/sessions/{uuid}/session.jsonl` — append-only: one `{"type":"header",...}` line, then one `{"type":"turn",...}` line per API round-trip, recording exact API usage, per-item `orig_chars`/`tomb_chars`/`tombstone`/`preview`. This is wet's own durable log, separate from Claude Code's transcript.
- **Originals are never touched**: wet only mutates the outbound HTTP request to Anthropic; Claude Code's own transcript JSONL under `~/.claude/projects/*/sessions/*.jsonl` is untouched and still holds the full original tool outputs, so compression is reversible/auditable after the fact from the CC side.
- Confirmed **security gap** (open issue #7, unfixed): all of the above is written with `0o755`/`0o644` permissions — world-readable on shared machines.

### API calls it makes

- To `api.anthropic.com` (or configured upstream): purely as a transparent forwarder for the user's own Claude Code traffic (no separate billing).
- The disabled `compressor/tier2.go` path, if ever turned on, would make its **own** direct `POST /v1/messages` calls using `ANTHROPIC_API_KEY` from the environment — a genuine side-channel API cost, which is presumably exactly why it ships disabled and the skill/subagent path (billed as normal session subagents) is the documented one.
- No calls to any non-Anthropic/third-party service anywhere in the codebase.

### Autocompact interaction

Documented as a deliberate design property, not a side effect: because Claude Code's autocompact trigger reads `usage.input_tokens` from the API response, and wet compresses *before* that request goes out, the API reports a smaller number and Claude Code's autocompact threshold is pushed further away. Quote: *"wet's goal is to prevent premature autocompact by keeping API-visible context lean."*

### Known operational gotchas (from README "Known Behaviors")

- **Claude Desktop app IPC bypass (CC ≥2.1.77):** when the Claude Desktop app is running, Claude Code routes API calls through it via Unix-socket IPC instead of direct HTTP, which bypasses `ANTHROPIC_BASE_URL` entirely — wet goes blind. Workaround: quit Claude Desktop before starting a `wet claude` session.
- **ToolSearch eager loading:** pointing `ANTHROPIC_BASE_URL` at a non-first-party host makes Claude Code load all tool schemas eagerly instead of deferring them (adds ~5-10K tokens, <1% of context). An `ENABLE_TOOL_SEARCH` env var can restore deferred loading but "adds identifiable signals to API requests that flag your setup as externally modified" — the maintainer explicitly recommends against using it, i.e., against trying to hide the proxy from Anthropic.
- **`wet serve` (standalone/Docker mode)** only tracks one active main session's inspect/compress state per proxy process — needs one container/port per concurrent IDE conversation.

## Design implications for our plugin

1. **Check whether native hooks now cover what wet needed a proxy for.** wet's entire reverse-proxy architecture is a workaround for a gap that Claude Code closed in **v2.1.121** (`PostToolUse` → `hookSpecificOutput.updatedToolOutput`, generalized from MCP-only to all tools). We're targeting v2.1.241. Before building anything proxy-shaped, prototype a `PostToolUse` hook that inspects `tool_response`/`tool_use_id`/`duration_ms` and returns `updatedToolOutput` directly — this avoids: the ToS-gray-area MITM-proxy framing wet has to devote a whole README section to defending; the Claude Desktop IPC bypass hole; the ToolSearch eager-load side effect; the "which session is the proxy even attached to" bug class that forced wet to add a mandatory salt/identity-verification phase; and the multi-process/port-management complexity in `cli/shim.go`. A hook runs in-process per Claude Code's own lifecycle and doesn't need a child-process wrapper, free-port allocation, or SSE re-parsing.
2. **Separate "mechanical/deterministic" from "judgment-driven" compression, and gate the latter behind a visible approval step.** wet's Tier 1 (regex per tool family, <5ms, no LLM) vs. Tier 2 (subagent rewrite, billed as a normal subagent call, hard token budgets, explicit anti-pass-through instructions) is a clean split worth copying. Notably wet found Tier-1 mechanical truncation actively harmful for two categories — Agent/Task returns (15-38% compression, "poor — dense analytical text doesn't respond to truncation") and Grep/Glob (head/tail truncation silently drops mid-list matches) — and hard-codes "always LLM-rewrite, never mechanically truncate" for both. Worth adopting that exact carve-out.
3. **Never auto-compress silently past a threshold without staged confirmation for anything content-losing.** wet's Phase 3 approval gate ("STOP. WAIT FOR USER. DO NOT AUTO-APPROVE") is deliberate, in all-caps, in the skill file — the maintainer explicitly frames this as better than autocompact precisely because it isn't a silent all-or-nothing cliff. Our plugin's "judgement-driven" requirement lines up with this; wet's implementation of it (structured profiler output → fixed ASCII plan → explicit y/n/edit) is a reasonable template.
4. **Hard-protect a small, named set of context regions unconditionally**, independent of staleness/token pressure: current-turn/fresh (≤3 turns) results, error outputs, images, anything already-compressed (idempotency marker), and — most relevant to "memory" framing — named identity/memory files (wet's SOUL.md/IDENTITY.md/USER.md/MEMORY.md convention). If our plugin's checkpoint files follow a similar naming convention, baking "never touch these regardless of pressure" into the compression logic (not just the prompt) avoids the failure mode where an LLM-driven compressor decides on its own that a memory file is stale.
5. **Ground every reported metric in the real API response, not local estimates.** wet estimates pre-compression size with a cheap `chars/3.3` heuristic but is careful to always report *savings* and *fill%* from Anthropic's actual `usage` block (parsed out of SSE `message_start`/`message_delta`, or the JSON pre-flight response on newer CC versions) — explicitly calling local estimates "not ground truth." If our plugin reports context-health/compaction numbers to the user, use the same discipline; wet's own changelog shows they had to fix an early version that used a heuristic that "overcounted by ~60%."
6. **Session-identity confusion is a real, previously-hit bug class or in multi-session, multi-agent setups** — build in an explicit "is this state actually for the session I think it's for" check from day one rather than retrofitting it (wet had to add Phase 0 after a documented misdiagnosis incident). This is especially relevant since our plugin will operate inside long, branching, subagent-spawning tasks like the ones described in this very research task.
7. **Decide deliberately whether to ship as a Skill+binary, an MCP server, or a formal `.claude-plugin` marketplace plugin.** wet chose Skill (behavior/instructions) + separate compiled binary (mechanism), explicitly not the plugin-marketplace format — this keeps it CLI-tool-agnostic (also markets itself for Codex/Cursor/Aider in a related repo) but means it's invisible to `/plugin` discovery. Since Jonathan's project is explicitly framed as "a Claude Code plugin," we should decide up front whether that means the formal plugin-manifest mechanism (discoverable, `/plugin install`-able) or a looser bundle like wet's — the two have different distribution and update-story implications.
8. **File permissions on any local state store**: default to `0700`/`0600`, not wet's `0755`/`0644` — this is a filed-and-unfixed issue on wet (context/session logs containing full tool outputs and token/model metadata, world-readable on shared machines).
9. **If we ever consider a "let a cheap model summarize between sessions" feature**, the `claude-remember` project surfaced in wet's own issue thread claims 81% token reduction via Haiku-summarization with layered retention (recent=detailed, old=compressed) — a candidate reference for prior art on the *between-session* (vs. wet's *within-session*) half of the problem, though we have not independently verified its claims or dissected its code.

## Sources

- [buildoak/wet](https://github.com/buildoak/wet) — main repository (cloned to `/tmp/research/wet`)
- [buildoak/wet — LICENSE](https://github.com/buildoak/wet/blob/main/LICENSE)
- [buildoak/wet — README.md](https://github.com/buildoak/wet/blob/main/README.md)
- [buildoak/wet — CHANGELOG.md](https://github.com/buildoak/wet/blob/main/CHANGELOG.md)
- [buildoak/wet — skill/SKILL.md](https://github.com/buildoak/wet/blob/main/skill/SKILL.md)
- [buildoak/wet — skill/references/architecture.md](https://github.com/buildoak/wet/blob/main/skill/references/architecture.md)
- [buildoak/wet — skill/references/heuristics.md](https://github.com/buildoak/wet/blob/main/skill/references/heuristics.md)
- [buildoak/wet — issue #7, Session Data Files World-Readable](https://github.com/buildoak/wet/issues/7)
- [Wet Claude: Let Claude Optimize Its Own Context — nickoak.com](https://www.nickoak.com/posts/wet-claude/)
- [anthropics/claude-code issue #32105 — PostToolUse hooks: allow `updatedToolOutput` for built-in tools](https://github.com/anthropics/claude-code/issues/32105)
- [anthropics/claude-code CHANGELOG.md](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md) (v2.1.121 entry: `updatedToolOutput` generalized to all tools)
- [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official) (checked for absence of "wet")
- Adjacent projects surfaced incidentally (not dissected): [MaxwellCalkin/sentinel-ai](https://github.com/MaxwellCalkin/sentinel-ai), [Dave-London/Pare](https://github.com/Dave-London/Pare), [Digital-Process-Tools/claude-remember](https://github.com/Digital-Process-Tools/claude-remember), [rtk-ai/rtk issue #260](https://github.com/rtk-ai/rtk) (permission-bypass bug referenced in the thread)
