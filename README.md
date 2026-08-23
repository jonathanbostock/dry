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

- **Events:** `UserPromptSubmit` (every prompt) and `PostToolUse` matcher `*` (the main path during long autonomous turns; throttled — a full check only every ≥25 tool calls or ≥60 s; a throttled call costs one interpreter start, ~70 ms measured on this box).
- **Signal:** context tokens (read cache-aware from the transcript tail's `message.usage`; after a compaction boundary the reading is "unknown" until the next assistant turn, never stale-high) against a fixed *reference budget* (`reference_window`, default 200,000 tokens) — deliberately independent of the model's real ceiling: degradation and prompt-cache economics set the budget, not the 1M window.
- **Trigger:** edge-triggered bands at 50/70/85% of the reference budget. An advisory is injected once per band, on upward crossing only; a downward jump means compaction happened and the bands re-arm.
- **Injected** (`additionalContext`, hard-capped under 1,000 chars — the three advisories measure 337–429 — plugin-generated text only): at 50%, the usage numbers, the top-3 largest tool results seen (tool and size), and a menu — update the ledger (dry:context-ledger skill), subagent the heavy reads, divert big outputs to files. At 70%: finish the current subtask, update the ledger, then recommend a boundary compaction (or `/clear`; rehydration is automatic). At 85%: checkpoint immediately, keep further output minimal, and propose `/compact` with ledger-derived instructions in the next user-facing message. Exact strings: `ADVISORIES` in [scripts/dry_watch.py](scripts/dry_watch.py).
- **Files written:** `/tmp/claude-dry/<session-id>/state.json` (throttle clock, last band, rolling top-K result sizes — tool names and byte counts only, never content).

### AVOID — `scripts/dry_guard.py` (reversible oversized-result diversion)

- **Event:** `PostToolUse`, matcher `Bash|Read|Grep|Glob|WebFetch|WebSearch`.
- **Trigger:** the response's one big field — Bash `stdout`, Read `file.content`, WebFetch `result` — over 16,000 chars (~4k tokens), and larger than head+tail, so a stub can never exceed the original.
- **Never diverts:** image results; unknown response shapes (only positively-identified shapes are rewritten, and Claude Code ignores malformed rewrites anyway — doubly fail-open); anything already stubbed. Error-ish Bash results (non-empty stderr, or interrupted) divert only reluctantly, at 3× the threshold, and stderr itself is never touched — errors stay visible.
- **Action:** what enters context is the first 6,000 chars, the marker, then the last 4,000 chars. The marker (exact template):

  ```
  [dry diverted 20,000 of 30,000 chars. <pointer>. Middle omitted — retrieve what you need rather than re-running the command.]
  ```

  The pointer depends on the tool. **Bash:** when native truncation already persisted the full output, the pointer cites Claude Code's own `persistedOutputPath` (no duplicate copy); otherwise dry archives to `<project>/.claude/dry/archive/<sid8>/<utc-stamp>-bash.txt` (3-line header: tool, timestamp, command). **Read:** points back at the source file with offset/limit instructions — the file *is* the archive; nothing is copied. **WebFetch:** archives the fetched result.
- **Reversal:** one `Read` or `Grep` of the pointed-at path. Nothing is ever lost, only relocated.

### RESET — `scripts/dry_checkpoint.py` + `scripts/dry_rehydrate.py`

- **`PreCompact`** (manual and auto): gzip-copies the full transcript to `<project>/.claude/dry/snapshots/<utc>-<sid8>-<trigger>.jsonl.gz`, pruning to the last 10. On an *auto* trigger (and only when the snapshot actually succeeded) it appends one line to the ledger — `- ⚠ <utc-iso>: auto-compact fired mid-task; pre-compact snapshot: <path>` — so the post-compaction agent learns an ungraceful compaction happened. Never blocks compaction (blocking an error-recovery auto-compact would fail the request).
- **`PostCompact`**: saves the native `compact_summary` to `snapshots/<utc>-<sid8>-summary-<trigger>.md` — an audit trail of what compaction actually kept. Side effect only; this event carries no injection channel.
- **`SessionStart` matcher `compact`**: injects the ledger content (capped at 6,000 chars, else head + truncation marker) behind the fixed preamble: "[dry] Context was just compacted. The native summary above may have dropped specifics. The task ledger below is authoritative (full pre-compact transcript snapshot: `<path>`). Re-read it before continuing."
- **`SessionStart` matcher `clear|resume`**: injects a one-line pointer only — ledger path, its `## Goal` first line, and its age — and only if the ledger exists and is under 7 days old. New tasks don't inherit a stale ledger's weight; resumed ones recover it in one `Read`.
- **`SessionStart` matcher `startup`**: deliberately not hooked — CLAUDE.md and auto-memory own cold starts.

### GATE — `scripts/dry_gate.py` (opt-in self-compaction for autonomous runs)

The model cannot trigger `/compact` — but a PreCompact hook can *defer* a proactive auto-compact (exit 2), and blocked attempts retry (live-verified: 5–6 PreCompact(auto) attempts per session). The gate inverts control:

- Launch autonomous runs with `DRY_GATE=1 claude --autocompact 300000` — past the window, a compaction is effectively always pending.
- The gate defers each auto attempt and leaves a marker; `dry_watch` injects a one-time in-context notice telling the agent a compaction is waiting on it.
- At a clean boundary the agent updates the ledger and runs `touch .claude/dry/compact-ok`; the next attempt (typically the next turn) passes through, consuming the flag one-shot.
- Failsafes, all pointing toward ALLOW: manual `/compact` is never blocked; a stale flag (>60 min) is ignored; if the agent never releases, the gate stands aside near the model's real window (90% by default, 200k window assumed for Haiku or `CLAUDE_CODE_DISABLE_1M_CONTEXT`); when it can't read the transcript, it allows; any internal error allows. Nothing can strand a session.

Live-verified end to end (`tests/smoke_gate.sh`, 8/8): six gate invocations, four deferred, **two agent-released compaction cycles in one session** — the agent saw the notice, touched the flag, compaction proceeded at the boundary, rehydration followed, markers cleaned up.

Default **off**: it changes native compaction behavior and relies on the skill-taught release protocol. Enable per-launch (`DRY_GATE=1`) or per-project (`gate_enabled` in config).

### Judgement — the `context-ledger` skill and two commands

[`skills/context-ledger/SKILL.md`](skills/context-ledger/SKILL.md) is the layer the deterministic legs exist to serve: it teaches the agent to keep a delta-based ledger at `<project>/.claude/dry/ledger.md` (Goal / Now / Done / Decisions / Files / Gotchas / Next), to recite Now+Next after each update, to answer each watch band with a fixed playbook, and to *propose* `/compact` with ledger-derived keep/drop instructions at task boundaries — the model cannot invoke `/compact` itself (the Skill tool exposes only a few built-ins such as `/init`; `/compact` is not among them — verified). `/dry:status` embeds a live report from `scripts/dry_status.py` and interprets it; `/dry:handoff [focus]` checkpoints the ledger and ends with a compact/clear/keep-going recommendation.

## Repo map

```
.claude-plugin/plugin.json       plugin manifest (name: dry, v0.1.0)
.claude-plugin/marketplace.json  this repo doubles as its own marketplace
hooks/hooks.json                 all event wiring, ${CLAUDE_PLUGIN_ROOT} paths, timeouts
scripts/_common.py               shared helpers: fail-open guard, config, transcript token read
scripts/dry_watch.py             SEE  — band advisories
scripts/dry_guard.py             AVOID — oversized-result diversion
scripts/dry_gate.py              GATE  — opt-in agent-released auto-compaction
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
| `snapshot_min_interval_seconds` | `DRY_SNAPSHOT_MIN_INTERVAL_SECONDS` | `60` |
| `ledger_pointer_max_age_days` | `DRY_LEDGER_POINTER_MAX_AGE_DAYS` | `7` |
| `gate_enabled` | `DRY_GATE_ENABLED` (alias: `DRY_GATE=1`) | `false` |
| `gate_flag_max_age_minutes` | `DRY_GATE_FLAG_MAX_AGE_MINUTES` | `60` |
| `gate_failsafe_fraction` | `DRY_GATE_FAILSAFE_FRACTION` | `0.9` |
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

Restart any running sessions afterwards — hook configuration is cached per session, so live sessions won't pick the hooks up. To remove: `claude plugin uninstall dry@dry`, then `claude plugin marketplace remove dry` (both verified against v2.1.241). Uninstalling leaves `<project>/.claude/dry/` and `/tmp/claude-dry/` behind; delete them by hand for a clean slate.

**First run in a project: expect silence.** Nothing is visible until a band trips or a >16k tool result appears; the ledger doesn't exist until the skill (or you) creates it; and on a first compaction with no ledger the agent gets a one-line nudge to create one. Quiet is the intended default.

The hooks can also be smoke-tested *without* installing: point a sandbox project's `.claude/settings.json` at the scripts by absolute path (the same wiring as `hooks/hooks.json`, minus `${CLAUDE_PLUGIN_ROOT}`). Plugin packaging is only needed for the skill, the commands, and distribution.

## Testing

```
uv run --no-project --with pytest pytest tests/ -q
```

Unit tests run every hook script as a subprocess on fixture stdin: band edge-triggering (up-crossings, down-reset, no re-fire), throttling, guard thresholds, protections and shape fidelity, checkpoint pruning, rehydrate caps and age gates, and the fail-open contract (malformed/huge/missing inputs, unwritable dirs ⇒ all exit 0). Fixtures were captured from a real probe session, not hand-written.

Two live smokes run real headless sessions in a sandbox project, wiring the hooks via `.claude/settings.json` (model pinned; each costs a few API calls):

- `tests/smoke_live.sh` (6/6 passed): an oversized `Read` produces the diversion stub in the actual session transcript; a watch advisory appears (tiny `DRY_REFERENCE_WINDOW`); `claude -p --resume` triggers the SessionStart(resume) ledger pointer; state dirs land 0700.
- `tests/smoke_compact.sh` (6/6 passed): forces a **real auto-compact** with `--autocompact 100000` and observes the full choreography — the PreCompact(auto) snapshot written and gunzipping cleanly, the ⚠ marker appended to the ledger (proving the `trigger` field), PostCompact's `compact_summary` audit file written (proving that field), and the post-compaction SessionStart injection. The compaction-path field names are live-verified, not doc-derived.
- `tests/smoke_gate.sh` (8/8 passed): the full gate protocol with a live agent — deferred auto-compacts retrying, the pending notice delivered, the agent releasing via `compact-ok` at a boundary, compaction proceeding, markers cleaned. Two agent-released compaction cycles observed in one session.

`claude plugin validate .` passes (run separately; not part of the smokes).

## Security posture

- Everything dry writes: dirs `0700`, files `0600`, enforced by umask — explicitly designed against wet's world-readable-state mistake ([buildoak/wet#7](https://github.com/buildoak/wet/issues/7): session logs at `0644`).
- Everything dry injects into context is plugin-generated text only: token counts, fixed phrases, and paths under dry's own directories. Tool output never round-trips into `additionalContext` — explicitly designed against the [claude-mem unsanitized-injection audit finding](https://github.com/thedotmack/claude-mem/issues/1251).
- The one exception is deliberate and bounded: post-compaction rehydration injects ledger content (capped at 6,000 chars) and the clear/resume pointer injects the ledger's Goal line (one line). The ledger is agent/user-authored project state, and it is length-capped.

## Limitations and non-features

- **No LLM compression of context** — the evidence says cost without quality gain ([arXiv:2605.18854](https://arxiv.org/abs/2605.18854)).
- **No direct `/compact` self-invocation** — platform limit (the Skill tool doesn't expose it). Interactively, the skill teaches proposing it to the user; for autonomous runs the opt-in gate inverts control — the agent *releases* a pending auto-compact at its chosen boundary, which is self-compaction in all but name.
- **No transcript surgery** — documented data-loss incidents in the wild; native `--resume` semantics may change underneath.
- **No statusline** — human-facing context-% is already natively solved.
- **Archives unpruned in v1** — disk is cheap; `/dry:status` reports archive size so you can clean up by hand.
- **No PreToolUse blocking or input rewriting** — a wrongly-blocked tool call is worse than a big result.
- **No Stop-hook injection** — it continues the conversation and risks loops.
- **No cross-session semantic memory** — a different, crowded problem; Claude Code's auto-memory already covers it.
- **No daemon, no MCP server, no DB, no dependencies** — hooks and files suffice.

## Known unknowns

- `tool_response` shapes for Grep/Glob/WebSearch were not captured by the fixture probe on this box (they are deferred tools here; Bash/Read/WebFetch were captured) — the guard rewrites only positively-identified shapes and passes everything else through untouched, so the miss costs coverage, not correctness.
- `additionalContext` size etiquette is undocumented upstream — dry self-caps under 1,000 chars (watch) and 6,000 chars (rehydrate).
- PreCompact on a *manual* `/compact` is unit-tested but not live-observed (the forced live compaction was an auto trigger; `-p` mode can't type `/compact`). The auto path — the one that matters for unattended long tasks — is live-verified end to end.
- Two accepted small races, both self-correcting within a turn: parallel tool calls can double-emit one band advisory (state is last-writer-wins), and between a compaction and the next assistant turn the token reading is "unknown" rather than a number.
- Per-call overhead measured on this box: ~70 ms for a throttled watch call, ~140 ms for watch+guard on a matched call (Python startup dominates). If that ever matters, narrow the PostToolUse matchers.
