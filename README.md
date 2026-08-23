# dry — deterministic context keeping for Claude Code

## What it is

dry keeps long Claude Code sessions working past the point where they normally rot: it shows the agent its own context usage at the moments that matter, keeps pathologically large tool results out of context (reversibly), snapshots the transcript before any compaction, and re-anchors the agent from a task ledger afterwards. It is deterministic end to end — no LLM calls, no daemon, no database, no dependencies beyond Python ≥3.10 stdlib — and every hook fails open, so the worst it can ever do is nothing. The name is a nod to [buildoak/wet](https://github.com/buildoak/wet) ("Wringing Excess Tokens"): wet compresses context *wetly* — LLM rewrites, lossy, approval-gated; dry keeps context *dry* in the first place — deterministic and reversible.

The problem it addresses: sessions degrade well before the window fills ([NoLiMa](https://arxiv.org/abs/2502.05167): associative recall below 50% of baseline by 32k tokens; [Chroma's context-rot study](https://research.trychroma.com/context-rot): degradation even on trivial tasks), while on 1M-window models native auto-compact fires far too late, with a generic summary that loses decisions, reasons, and next steps. Design and evidence in full: [DESIGN.md](DESIGN.md).

## Why these mechanisms

| Choice | Evidence |
|---|---|
| No LLM summarization/compression tier | The one controlled study for coding agents: LLM condensers cost +24–94% with *no* quality gain; masking old tool outputs was the only net-positive strategy ([arXiv:2605.18854](https://arxiv.org/abs/2605.18854)). |
| Reversible diversion with pointer stubs | [Manus "restorable compression"](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus): keep the path, drop the content. Malformed `updatedToolOutput` is ignored by Claude Code, so the rewrite path is fail-open by construction. |
| Ledger = itemized deltas, not rewrites | [ACE](https://arxiv.org/abs/2510.04618): full rewrites cause context collapse via brevity bias. Plan-file-as-external-memory is the community's single most reinvented pattern. |
| Compact at task boundaries, by judgement | [Context-Folding](https://arxiv.org/abs/2510.11967): 10× smaller active context at equal or better quality than blanket summarization; community rule: never let auto-compact fire mid-task. |
| Checkpoint before compaction, rehydrate after | [context-mode](https://github.com/mksglu/context-mode) proved the PreCompact-snapshot → SessionStart-restore mechanic. Auto-compact passes empty `custom_instructions` ([anthropics/claude-code#14160](https://github.com/anthropics/claude-code/issues/14160)), so the native summary cannot be steered — the ledger must carry the state. |
| Inject sparingly, sanitize always | [claude-mem security audit](https://github.com/thedotmack/claude-mem/issues/1251): unsanitized tool content flowing into `additionalContext` is a persistent prompt-injection vector. dry injects only self-generated text. |

## How it works

Three deterministic legs plus a judgement layer. Every hook wraps its body in a fail-open guard: any exception → exit 0, empty stdout; hooks never block, never deny, never exit 2. Diagnostics from swallowed errors go to `/tmp/claude-dry/debug.log` (`DRY_DEBUG=1` mirrors them to stderr).

### SEE — `scripts/dry_watch.py` (agent-side context visibility)

- **Events:** `UserPromptSubmit` (every prompt) and `PostToolUse` matcher `*` (the main path during long autonomous turns; throttled — a full check only every ≥25 tool calls or ≥60 s, otherwise it exits in milliseconds).
- **Signal:** context tokens (read from the transcript tail's `message.usage`) over a *reference window* of `min(model window, 200k)` — degradation and prompt-cache economics set the budget, not the 1M ceiling.
- **Trigger:** edge-triggered bands at 50/70/85%. An advisory is injected once per band, on upward crossing only; a downward jump means compaction happened and the bands re-arm.
- **Injected** (`additionalContext`, self-capped ≤ ~700 chars, plugin-generated text only): at 50%, a usage report, the top-3 largest tool results seen (tool, size, when), and a menu — update ledger, subagent the heavy reads, divert big outputs to files. At 70%: "finish the current subtask, update the ledger, then recommend the user compact at this boundary; auto-compact will not be graceful." At 85%: "checkpoint NOW; propose `/compact` to the user with instructions derived from the ledger."
- **Files written:** `/tmp/claude-dry/<session-id>/state.json` (throttle clock, last band, rolling top-K result sizes — tool names and byte counts only, never content).

### AVOID — `scripts/dry_guard.py` (reversible oversized-result diversion)

- **Event:** `PostToolUse`, matcher `Bash|Read|Grep|Glob|WebFetch|WebSearch`.
- **Trigger:** serialized `tool_response` over 16,000 chars (~4k tokens).
- **Never diverts:** image results; error-ish results (Bash with non-empty stderr, or interrupted — threshold ×3, and stderr is always preserved in full up to the tail cap, because errors must stay visible); anything already stubbed; any response shape it does not positively recognize (unknown shape ⇒ exit 0 — and Claude Code ignores malformed rewrites anyway, so this path is doubly fail-open).
- **Action:** the full original is written to `<project>/.claude/dry/archive/<sid8>/<seq>-<tool>.txt` with a 3-line header (tool, timestamp, input summary). What enters context instead is the first 6,000 chars, then the stub line, then the last 4,000 chars. The stub reads:

  ```
  …
  [dry diverted N chars → <path> — Read/Grep it if you need the rest]
  ```

- **Reversal:** one `Read` or `Grep` of the archived file. Nothing is ever lost, only relocated.

### RESET — `scripts/dry_checkpoint.py` + `scripts/dry_rehydrate.py`

- **`PreCompact`** (manual and auto): gzip-copies the full transcript to `<project>/.claude/dry/snapshots/<utc>-<sid8>-<trigger>.jsonl.gz`, pruning to the last 10. On an *auto* trigger it additionally appends one line to the ledger — `⚠ auto-compact fired <utc> — snapshot: <path>` — so the post-compaction agent learns an ungraceful compaction happened. Never blocks compaction (blocking an error-recovery auto-compact would fail the request).
- **`PostCompact`**: saves the native `compact_summary` to `snapshots/<utc>-summary.md` — an audit trail of what compaction actually kept. Side effect only; this event carries no injection channel.
- **`SessionStart` matcher `compact`**: injects the ledger content (capped at 6,000 chars, else head + pointer) behind a fixed preamble: "Compaction just occurred; the native summary may have dropped specifics. The ledger below and snapshot on disk are authoritative. Re-read before continuing."
- **`SessionStart` matcher `clear|resume`**: injects a one-line pointer only — ledger path, its `## Goal` first line, and its age — and only if the ledger exists and is under 7 days old. New tasks don't inherit a stale ledger's weight; resumed ones recover it in one `Read`.
- **`SessionStart` matcher `startup`**: deliberately not hooked — CLAUDE.md and auto-memory own cold starts.

### Judgement — the `context-ledger` skill and two commands

[`skills/context-ledger/SKILL.md`](skills/context-ledger/SKILL.md) is the layer the deterministic legs exist to serve: it teaches the agent to keep a delta-based ledger at `<project>/.claude/dry/ledger.md` (Goal / Now / Done / Decisions / Files / Gotchas / Next), to recite Now+Next after each update, to answer each watch band with a fixed playbook, and to *propose* `/compact` with ledger-derived keep/drop instructions at task boundaries — the model cannot invoke `/compact` itself (the Skill tool excludes built-ins; verified). `/dry:status` embeds a live report from `scripts/dry_status.py` and interprets it; `/dry:handoff [focus]` checkpoints the ledger and ends with a compact/clear/keep-going recommendation.

## Repo map

```
.claude-plugin/plugin.json       plugin manifest (name: dry, v0.1.0)
.claude-plugin/marketplace.json  this repo doubles as its own marketplace
hooks/hooks.json                 all event wiring, ${CLAUDE_PLUGIN_ROOT} paths, timeouts
scripts/_common.py               shared helpers: fail-open guard, config, transcript token read
scripts/dry_watch.py             SEE  — band advisories
scripts/dry_guard.py             AVOID — oversized-result diversion
scripts/dry_checkpoint.py        RESET — PreCompact/PostCompact snapshots
scripts/dry_rehydrate.py         RESET — SessionStart ledger injection
scripts/dry_status.py            report backing /dry:status and /dry:handoff
skills/context-ledger/SKILL.md   the judgement layer
commands/status.md               /dry:status
commands/handoff.md              /dry:handoff
tests/                           pytest suite + fixtures captured from a live probe session
research/                        the six research tracks behind DESIGN.md
DESIGN.md                        the design contract (v1, 2026-08-23)
```

## Configuration

Defaults ← `<project>/.claude/dry/config.json` ← `DRY_*` environment variables (env wins). All keys:

| config.json key | env var | default |
|---|---|---|
| `reference_window` | `DRY_REFERENCE_WINDOW` | `200000` |
| `bands` | `DRY_BANDS` (JSON list) | `[0.50, 0.70, 0.85]` |
| `watch_min_calls` | `DRY_WATCH_MIN_CALLS` | `25` |
| `watch_min_seconds` | `DRY_WATCH_MIN_SECONDS` | `60` |
| `guard_threshold_chars` | `DRY_GUARD_THRESHOLD_CHARS` | `16000` |
| `guard_head_chars` | `DRY_GUARD_HEAD_CHARS` | `6000` |
| `guard_tail_chars` | `DRY_GUARD_TAIL_CHARS` | `4000` |
| `guard_error_multiplier` | `DRY_GUARD_ERROR_MULTIPLIER` | `3` |
| `snapshots_keep` | `DRY_SNAPSHOTS_KEEP` | `10` |
| `ledger_pointer_max_age_days` | `DRY_LEDGER_POINTER_MAX_AGE_DAYS` | `7` |
| `disable` | `DRY_DISABLE` | `false` |
| `disable_watch` | `DRY_DISABLE_WATCH` | `false` |
| `disable_guard` | `DRY_DISABLE_GUARD` | `false` |
| `disable_checkpoint` | `DRY_DISABLE_CHECKPOINT` | `false` |
| `disable_rehydrate` | `DRY_DISABLE_REHYDRATE` | `false` |

### State file locations

- `/tmp/claude-dry/<session-id>/` — ephemeral per-session state (throttle clock, bands, top-K sizes); `/tmp/claude-dry/debug.log` collects swallowed-error diagnostics.
- `<project>/.claude/dry/` — durable: `ledger.md`, `config.json`, `archive/`, `snapshots/`.

Suggested project `.gitignore` entry: ignore `.claude/dry/` (archives and snapshots are bulky and session-specific) — except `ledger.md` if you want ledger history in git.

## Install (after review)

```
claude plugin marketplace add /workspace/claude-memory-management
claude plugin install dry@dry --scope user
```

Restart any running sessions afterwards — hook configuration is cached per session, so live sessions won't pick the hooks up. To remove: `claude plugin uninstall dry@dry`, then `claude plugin marketplace remove dry` (confirm exact subcommand spellings against `claude plugin --help` for your version).

The hooks can also be smoke-tested *without* installing: point a sandbox project's `.claude/settings.json` at the scripts by absolute path (the same wiring as `hooks/hooks.json`, minus `${CLAUDE_PLUGIN_ROOT}`). Plugin packaging is only needed for the skill, the commands, and distribution.

## Testing

```
uv run --no-project --with pytest pytest tests/ -q
```

Unit tests run every hook script as a subprocess on fixture stdin: band edge-triggering (up-crossings, down-reset, no re-fire), throttling, guard thresholds, protections and shape fidelity, checkpoint pruning, rehydrate caps and age gates, and the fail-open contract (malformed/huge/missing inputs, unwritable dirs ⇒ all exit 0). Fixtures were captured from a real probe session, not hand-written.

The live smoke (a sandbox project wiring the hooks via `.claude/settings.json`) covered: a huge `seq` output producing a stub in the transcript plus the archive file on disk; `DRY_REFERENCE_WINDOW=3000` making a band advisory appear; a session with a ledger followed by `claude -p --resume` showing the injected pointer; and `claude plugin validate .` passing.

## Security posture

- Everything dry writes: dirs `0700`, files `0600`, enforced by umask — explicitly designed against wet's world-readable-state mistake ([buildoak/wet#7](https://github.com/buildoak/wet/issues/7): session logs at `0644`).
- Everything dry injects into context is plugin-generated text only: token counts, fixed phrases, and paths under dry's own directories. Tool output never round-trips into `additionalContext` — explicitly designed against the [claude-mem unsanitized-injection audit finding](https://github.com/thedotmack/claude-mem/issues/1251).
- The one exception is deliberate and bounded: post-compaction rehydration injects ledger content (capped at 6,000 chars) and the clear/resume pointer injects the ledger's Goal line (one line). The ledger is agent/user-authored project state, and it is length-capped.

## Limitations and non-features

- **No LLM compression of context** — the evidence says cost without quality gain ([arXiv:2605.18854](https://arxiv.org/abs/2605.18854)).
- **No `/compact` self-invocation** — platform limit: the Skill tool excludes built-in commands, so the skill teaches proposing it to the user instead.
- **No transcript surgery** — documented data-loss incidents in the wild; native `--resume` semantics may change underneath.
- **No statusline** — human-facing context-% is already natively solved.
- **Archives unpruned in v1** — disk is cheap; `/dry:status` reports archive size so you can clean up by hand.
- **No PreToolUse blocking or input rewriting** — a wrongly-blocked tool call is worse than a big result.
- **No Stop-hook injection** — it continues the conversation and risks loops.
- **No cross-session semantic memory** — a different, crowded problem; Claude Code's auto-memory already covers it.
- **No daemon, no MCP server, no DB, no dependencies** — hooks and files suffice.

## Known unknowns

- `tool_response` shapes for Grep/Glob were not captured by the fixture probe on this box (Bash/Read/WebFetch were) — the guard rewrites only positively-identified shapes and passes everything else through untouched, so the miss costs coverage, not correctness.
- `additionalContext` size etiquette is undocumented upstream — dry self-caps at ~700 chars (watch) and 6,000 chars (rehydrate).
- PreCompact firing on manual `/compact` had a disputed upstream issue — it is observed in the live smoke rather than assumed, and the checkpoint is belt-and-braces anyway: the ledger alone suffices for recovery.
