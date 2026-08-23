# dry — deterministic context keeping for Claude Code

*Design v1, 2026-08-23. Synthesized from six research tracks (see `research/`); supersedes `DESIGN-DRAFT.md`. Target: Claude Code v2.1.241, plugin built in this repo, **not installed** until Jonathan reviews.*

The name is a nod to [buildoak/wet](https://github.com/buildoak/wet) ("Wringing Excess Tokens"), whose proxy-based approach v2.1.121 made obsolete: where wet compresses context *wetly* (LLM rewrites, lossy, approval-gated), dry keeps context *dry* in the first place — deterministic, reversible, no LLM calls, no daemon.

## 1. Problem

Long Claude Code sessions degrade well before the context window fills: context rot from ~32k tokens (NoLiMa), the "dumb zone" past ~40% fill (Horthy), stale tool results diluting attention, and — on 1M-window models like Fable 5 — native auto-compact not firing until ~the limit, long after quality and prompt-cache economics have collapsed. The current remedy (manual `/compact` "every now and then") fires at arbitrary moments with generic summarization that loses exactly the state that matters: decisions and their reasons, file-map knowledge, gotchas, the next step.

The agent should manage its own context: automatically where deterministic, by its own judgement where it matters. Three structural facts (all verified against v2.1.241 docs/changelog) shape the design:

1. **The live context is append-only to plugins.** No hook deletes past content. Native compaction/microcompaction is the only eraser, and the model cannot invoke `/compact` itself (Skill tool explicitly excludes it). Transcript surgery + `--resume` exists in the wild but has documented data-loss failure modes — out of scope.
2. **`PostToolUse` → `hookSpecificOutput.updatedToolOutput` (v2.1.121) rewrites what enters context, for all tools.** Entry-time diversion is the one true "cleanup" primitive — and it's cache-friendly (rewrites before append; never invalidates an existing prefix).
3. **The agent is blind to its own context usage.** Statuslines show it to the human (`context_window.*`); no native mechanism shows it to the model. Hooks can compute it from the transcript and inject it.

## 2. Evidence base (what the research settled)

| Decision | Evidence |
|---|---|
| **No LLM summarization/compression tier** | Only controlled study for coding agents: LLM condensers +24–94% cost, *no* quality gain; masking tool outputs was the only net-positive (arXiv 2605.18854). wet's Tier-2 exists but is approval-gated for good reason. |
| **Reversible, deterministic diversion with pointer stubs** | Manus "restorable compression" (keep path, drop content); literature pattern #1; malformed `updatedToolOutput` is *ignored* by Claude Code → fail-open by construction. |
| **Ledger = itemized deltas, not rewrites** | ACE: full rewrites cause "context collapse" + brevity bias. Community: plan-file-as-external-memory is the single most reinvented pattern. |
| **Compact at task boundaries, by judgement** | Context-Folding (10× smaller active context at equal/better quality vs blanket summarization); community "never let auto-compact fire mid-task". |
| **Checkpoint before compaction, rehydrate after** | context-mode (verified by code read): PreCompact snapshot → SessionStart(compact) restore. Auto-compact passes empty `custom_instructions` to PreCompact (#14160) — so the summary can't be shaped; the ledger must carry the state instead. |
| **Inject sparingly, sanitize always** | claude-mem security audit: unsanitized tool content in `additionalContext` = persistent prompt-injection vector. dry injects only self-generated text: numbers, fixed phrases, paths under its own dirs. |
| **No statusline, no daemon, no MCP server, no DB** | Context-% for humans is natively solved (Jonathan's statusline already shows it). Daemons (cozempic) and MCP servers add always-on cost and jank; hooks + files suffice. |

## 3. Architecture — three legs plus a judgement layer

```
            ┌──────────────────────────────────────────────────┐
            │  SKILL: context-ledger  (judgement: what/when)    │
            └───────▲──────────────▲───────────────▲───────────┘
                    │ advisories   │ boundary       │ recovery
   ┌────────────────┴──┐  ┌────────┴────────┐  ┌────┴─────────────┐
   │ SEE: dry_watch    │  │ AVOID: dry_guard │  │ RESET: checkpoint │
   │ context % → agent │  │ oversized results│  │ + rehydrate       │
   │ (UserPromptSubmit │  │ → archive + stub │  │ (PreCompact,      │
   │  + PostToolUse,   │  │ (PostToolUse,    │  │  PostCompact,     │
   │  edge-triggered)  │  │  updatedToolOutput)│ │  SessionStart)   │
   └───────────────────┘  └─────────────────┘  └──────────────────┘
```

- **SEE** — the agent gets context-usage signal at judgement-relevant moments (band crossings, not every call).
- **AVOID** — pathologically large tool results never enter context whole; the full text lands on disk, a head+tail stub with the path enters context instead. Reversible by one `Read`/`Grep`.
- **RESET** — before any compaction the full transcript is snapshotted; after compaction/clear/resume the ledger re-anchors the agent automatically.
- **Judgement layer** — a skill teaching the agent to maintain a delta-based ledger, respond to watch advisories, recognize task boundaries, and recommend `/compact <instructions>` to the user at the right moment (it cannot run `/compact` itself — verified limitation).

## 4. Components

Everything Python 3 (≥3.10) stdlib-only, one shared helper module. Every hook wraps its body in a fail-open guard: any exception → exit 0, silent (diagnostic line to the state dir's `debug.log`). Hooks never block, never deny, never exit 2. All files we create: dirs `0700`, files `0600` (wet's known flaw was world-readable state).

### 4.1 `scripts/_common.py` (shared, written by orchestrator)
- `fail_open(main)` wrapper; `read_stdin_json()`.
- Paths: state dir `/tmp/claude-dry/<session_id>/` (ephemeral, per-session); project dir `<cwd>/.claude/dry/` (ledger, archive, snapshots — durable).
- `load_config(cwd)`: defaults ← optional `<cwd>/.claude/dry/config.json` ← `DRY_*` env vars.
- `transcript_context_tokens(path)`: read tail (last 1 MiB), scan backwards for the last main-chain (`isSidechain` falsy) assistant record with `message.usage`; return `input_tokens + cache_read_input_tokens + cache_creation_input_tokens (+ output_tokens)` and the model id. Returns `None` on any doubt.

### 4.2 SEE — `scripts/dry_watch.py`
- **Events:** `UserPromptSubmit` (every prompt — fresh signal at turn start), `PostToolUse` matcher `"*"` (long autonomous turns have no user prompts — this is the main path; **throttled**: full check only if ≥25 tool calls or ≥60 s since last check, else exit 0 in <20 ms via a tiny state read).
- **Signal:** `pct = context_tokens / reference_window`. `reference_window` = min(model window, `DRY_REFERENCE_WINDOW`, default **200_000**) — degradation and cache economics, not the 1M ceiling, set the budget (research: rot from 32k; dumb zone ≥40%).
- **Edge-triggered bands** (default 50 / 70 / 85 %): inject only on upward crossing, once per band per session (downward jump ⇒ compaction happened ⇒ reset bands). Escalating advisories, each ≤ ~700 chars, all plugin-generated text only:
  - **50%** — usage report + top-3 largest tool results seen (tool + size + when) + menu: update ledger, subagent the heavy reads, divert big outputs to files.
  - **70%** — "finish the current subtask, update the ledger, then recommend the user compact at this boundary; auto-compact will not be graceful."
  - **85%** — "checkpoint NOW; propose `/compact` to the user with instructions derived from the ledger."
- **State:** `/tmp/claude-dry/<sid>/state.json` — last check monotonic time, calls-since-check, last band, rolling top-K largest results (tool name + byte size only, no content).
- **Output:** `{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": "..."}}`.

### 4.3 AVOID — `scripts/dry_guard.py`
- **Event:** `PostToolUse`, matcher `Bash|Read|Grep|Glob|WebFetch|WebSearch`.
- **Trigger:** serialized `tool_response` > threshold (default **16_000 chars** ≈ 4k tokens).
- **Protections (never divert):** `isImage`; error-ish results (Bash with non-empty `stderr` or interrupted → threshold ×3, and stderr is always preserved in full up to a tail cap — errors must stay visible, per Manus and wet); anything already stubbed; tools whose response shape we don't positively recognize (unknown shape ⇒ exit 0; and Claude Code ignores malformed rewrites anyway — double fail-open).
- **Action:** write full original to `<cwd>/.claude/dry/archive/<sid8>/<seq>-<tool>.txt` with a 3-line header (tool, timestamp, input summary e.g. the command/path); emit `updatedToolOutput` matching the tool's shape with: first `HEAD` chars (default 6_000) + `\n…\n[dry diverted N chars → <path> — Read/Grep it if you need the rest]\n` + last `TAIL` chars (default 4_000).
- **Shapes:** Bash `{stdout, stderr, interrupted, isImage}` (documented); Read/Grep/Glob/WebFetch/WebSearch shapes captured empirically by the fixture probe (§7) before implementation; only positively-identified shapes are rewritten.

### 4.4 RESET — `scripts/dry_checkpoint.py` + `scripts/dry_rehydrate.py`
- **`PreCompact` (manual|auto):** gzip-copy the transcript to `<cwd>/.claude/dry/snapshots/<utc>-<sid8>-<trigger>.jsonl.gz`; prune to last 10. On **auto** trigger additionally append one line to the ledger: `⚠ auto-compact fired <utc> — snapshot: <path>` (the post-compact agent will see it via rehydration). Never blocks compaction (blocking an error-recovery auto-compact fails the request — verified).
- **`PostCompact` (manual|auto):** save `compact_summary` to `snapshots/<utc>-summary.md` — an audit trail of what native compaction actually kept. No injection (event has none; side-effect only).
- **`SessionStart`:**
  - matcher `compact`: inject ledger content (cap 6_000 chars, else head + pointer) + fixed preamble: "Compaction just occurred; the native summary may have dropped specifics. The ledger below and snapshot on disk are authoritative. Re-read before continuing."
  - matcher `clear|resume`: pointer-only (one line): ledger path + its `## Goal` first line + age — *if* the ledger exists and is <7 days old. New tasks shouldn't inherit a stale ledger's full weight; resumed ones get it in one `Read`.
  - matcher `startup`: **not hooked** — CLAUDE.md / auto-memory already own cold starts; don't double-inject.

### 4.5 Judgement — `skills/context-ledger/SKILL.md`
Model-invocable (auto-triggered by description match; also nudged by watch advisories). Teaches:
- **Ledger** at `<cwd>/.claude/dry/ledger.md`, strict template: `## Goal` / `## Now` / `## Done` (append-only one-liners) / `## Decisions` (what + *why*) / `## Files` (path → why it matters) / `## Gotchas` / `## Next`. **Itemized deltas, never full rewrites** (ACE); fold `Done` lines older than the current subtask into one line; keep ≤ ~150 lines total.
- **Recitation:** after each update, re-read `Now` + `Next` — re-anchors attention (Manus todo.md pattern).
- **When:** task start, each decision, subtask completion, before risky operations, on any watch advisory, before recommending compaction.
- **Task-boundary playbook:** tests green / commit made / subtask closed ⇒ the *right* moment to recommend `/compact` — with instructions derived from the ledger (e.g. `/compact keep: decisions D1-D4, file map, gotchas; drop: exploration transcripts`). The skill is explicit that the model cannot run `/compact` itself.
- **Archive awareness:** diverted outputs live under `.claude/dry/archive/` — `Grep` them back on demand.

### 4.6 Commands
- **`/dry:status`** — embeds `!`-preprocessed output of `scripts/dry_status.py`: usage %, band, top hogs, ledger age/size, snapshots, archive size, and one suggested next action. Model-invocable (the agent can check itself mid-task).
- **`/dry:handoff [focus]`** — instructs the agent: update ledger per skill → run status → emit a recommendation block for the user (`/compact <derived instructions>` | `/clear` (rehydrate is automatic) | keep going). No side effects beyond the ledger write.

### 4.7 Packaging
`.claude-plugin/plugin.json` (name **dry**, v0.1.0) + `.claude-plugin/marketplace.json` (repo is its own marketplace) + `hooks/hooks.json` (all wiring, `${CLAUDE_PLUGIN_ROOT}` paths, explicit timeouts: watch 10 s, guard 30 s, checkpoint 60 s, rehydrate 10 s). Install after review:
```
claude plugin marketplace add /workspace/claude-memory-management
claude plugin install dry@dry        # exact syntax verified during build
```

## 5. Configuration

`<cwd>/.claude/dry/config.json`, env `DRY_*` overrides. Keys (defaults): `reference_window` (200000), `bands` ([0.50, 0.70, 0.85]), `watch_min_calls` (25), `watch_min_seconds` (60), `guard_threshold_chars` (16000), `guard_head_chars` (6000), `guard_tail_chars` (4000), `guard_error_multiplier` (3), `snapshots_keep` (10), `ledger_pointer_max_age_days` (7), `disable` per-component flags.

## 6. Deliberate non-features (each fought for its life)

- **No LLM/subagent compression of context** — evidence says cost without quality (§2). The `prompt`/`agent` hook types stay unused.
- **No transcript surgery** — real data-loss incidents (claude-code-cmv #11); native `--resume` semantics may change under us.
- **No PreToolUse blocking or input rewriting** — advisory over blocking; a wrongly-blocked tool call is worse than a big result. Revisit only with evidence.
- **No Stop-hook injection** — it continues the conversation (loop risk); the goal feature already owns that space.
- **No statusline** — natively solved; Jonathan's is already good.
- **No cross-session semantic memory** — claude-mem's crowded lane, different problem; Claude Code's auto-memory + this box's memory directory already cover it.
- **No daemon, no MCP server, no DB, no dependencies.**

## 7. Test plan

1. **Unit** (pytest, `uv run --no-project --with pytest`): every hook script run as a subprocess on fixture stdin — band edge-triggering (up, down-reset, no re-fire), throttling, guard thresholds/protections/shape fidelity, checkpoint pruning, rehydrate caps and age gates, malformed/huge/missing inputs, unwritable dirs ⇒ all exit 0.
2. **Fixture probe** (once, orchestrator, before guard implementation): sandbox project + a capture hook dumping raw `PostToolUse` payloads; scripted `claude -p` run (pinned `--model claude-fable-5`) exercising Bash/Read/Grep/Glob/WebFetch ⇒ real `tool_response` shapes into `tests/fixtures/`.
3. **Live smoke** (orchestrator, after build): sandbox project wiring the hooks via `.claude/settings.json` (plugin packaging not needed for hook mechanics): (a) huge `seq` output ⇒ transcript shows stub + archive file exists; (b) `DRY_REFERENCE_WINDOW=3000` ⇒ band advisory appears; (c) session with ledger, then `claude -p --resume` ⇒ pointer injected. Plus `claude plugin validate .`.

## 8. Risks / open items

- `tool_response` shapes for Read/Grep/Glob/WebFetch are undocumented → probe-first, rewrite only recognized shapes, fail-open otherwise.
- `additionalContext` size etiquette is undocumented → self-cap at ~700 chars (watch) / 6k (rehydrate).
- PreCompact reliability on manual `/compact` had a disputed upstream issue → smoke-tests observe it; checkpoint is belt-and-braces anyway (ledger alone suffices for recovery).
- Throttle counter writes on every PostToolUse (~10–30 ms Python startup per tool call) — measure in smoke; if noticeable, raise matcher specificity or sampling.
- Multiple concurrent sessions in one project share the ledger by design (rare here); entries are timestamped.

## 9. As-built notes (post-review, same day)

Where the build deviated from §4, deliberately and review-approved — the code is the contract now:

- **Guard:** marker template finalized in `dry_guard.py` (supersedes §4.3's sketch); extra trigger condition `len > head+tail` so a stub can never exceed the original; Bash reuses the native `persistedOutputPath` instead of duplicating archives; Read points back at the source file (no copy); archives named `<utc-stamp>-<tool>.txt` with O_EXCL collision suffixes.
- **Watch:** fresh-session state initializes the throttle clock, so a session's first PostToolUse throttles (UserPromptSubmit covers turn starts); hogs track (tool, size) only; advisory cap is 999 chars (the three advisories measure 337–429); a throttled call costs ~70 ms, not the hoped <20 ms — interpreter startup dominates.
- **Reference window:** implemented as a flat configured budget (default 200k), not `min(model window, 200k)` — same effect on every current model, one less moving part.
- **Checkpoint:** the auto-compact ledger marker is written only when the snapshot succeeded; PostCompact archives the native `compact_summary` (field live-verified by `tests/smoke_compact.sh`, which forces a real auto-compact via `--autocompact 100000`).
- **_common hardening from adversarial review:** compaction boundaries in the transcript now yield postTokens or "unknown" (never stale-high); config rejects bools for numeric keys; sub-1MiB transcripts keep their first line; every hook survives even import failure (exit 0); ledger reads are bounded (64 KiB) before capping.
- **Review outcome:** no critical or major findings; seven minors — five fixed, two accepted and documented (parallel-call advisory double-emit; archives unpruned). Final state: 86 unit tests, two live smokes (6/6 and 6/6, the second forcing a real auto-compact), `claude plugin validate` clean.

## 10. Addendum: the self-compaction gate (same day)

Jonathan asked for judgement-call self-compaction in multi-day autonomous (/goal) runs. Direct triggering stays impossible (§4.5), but a live probe established two facts: a PreCompact hook exit-2 skips a proactive auto-compact, and **blocked attempts retry** (five PreCompact(auto) invocations observed in one probe session; the PreCompact payload carries no token fields — `trigger`, `custom_instructions`, session/transcript/cwd only). That makes inverted control viable, shipped as `dry_gate.py` (opt-in, `DRY_GATE=1`):

- Low `--autocompact` window ⇒ a compaction is effectively always pending past the threshold.
- Gate defers `auto` attempts (never `manual`), leaving a pending marker; `dry_watch` turns the marker into a one-time in-context notice (mtime-deduped) so the agent knows to wrap up.
- Agent releases at a boundary via a one-shot `compact-ok` flag (60-min staleness cap, always consumed).
- Failsafes, all toward ALLOW: blind ⇒ allow; ≥90% of the model's real window ⇒ allow; any error ⇒ allow (fail_open). `dry_checkpoint` gained a 60 s snapshot throttle so retrying blocked attempts don't re-gzip the transcript each turn.

Live verification: `tests/smoke_gate.sh` 8/8 — six gate invocations, four deferred, two agent-released compaction cycles in one session (notice delivered → agent touched the flag → compaction at the boundary → rehydration). Suite: 100 unit tests.
