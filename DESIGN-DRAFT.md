# Design draft (pre-research hypotheses)

*Written 2026-08-23, before research results landed. Purpose: capture design intuitions now so they can be checked against evidence, not to constrain the design. The real DESIGN.md gets written after synthesis.*

## Problem statement

Jonathan runs long Claude Code sessions on this pod. Current context hygiene is manual `/compact` "every now and then" — which:
- fires at arbitrary points (or auto-fires at a threshold mid-task, at the worst possible moment),
- uses generic summarization that loses task-specific state (exact file paths, decisions, gotchas, test commands),
- leaves the agent unaware of its own context consumption until it's too late,
- keeps stale tool results (old file reads, huge command outputs) burning attention long after they stop being useful.

Goal: the agent manages its own context — automatically where possible, by its own judgement where it matters. Build as a reviewable plugin in this directory; do not install.

## Hypotheses to check against research

H1. **Context is append-only for a live session.** A plugin cannot delete past tool results from the live conversation; only native machinery (compaction, microcompaction/context-editing) can. If true, the plugin's levers are: (a) keep junk out, (b) externalize state to files, (c) make resets (compact/clear) cheap and lossless, (d) give the agent visibility + judgement triggers. → Verify with hooks-api agent: any event that can *remove* content? Transcript surgery + resume — does anyone do it reliably?

H2. **Visibility is the keystone.** The agent can't exercise judgement about context it can't see. A hook that periodically injects "you are at N% context; you've spent X tokens on tool results in the last hour" turns every downstream behavior from ritual into judgement. → Verify: which hook events can inject context (UserPromptSubmit? PostToolUse additionalContext?), what the transcript usage fields expose, injection frequency/size etiquette.

H3. **A task ledger beats a better summarizer.** A continuously-maintained, structured working-state file (goal, plan state, decisions+why, file map, gotchas, next actions) written *by the agent as it works* preserves exactly what generic compaction loses. Compact/clear then becomes cheap: rehydrate from the ledger. → Check community practice for existing ledger formats (PLAN.md/PROGRESS.md/handoff docs) and literature (Manus todo.md recitation; MemGPT self-editing memory).

H4. **When-to-compact matters as much as how.** Compacting at a task boundary (tests green, commit made) with task-aware instructions beats compacting at 95% mid-edit with generic instructions. If the model can invoke /compact itself (SlashCommand tool?) the plugin can make this fully autonomous; if not, it nudges the user at the right moment. → Verify SlashCommand tool capabilities for built-ins.

H5. **Prevention beats cleanup.** Most bloat is predictable at call time: reading a 4000-line file whole, running a command with unbounded output, cat-ing logs. Advisory guardrails (PostToolUse "that result was 18k tokens" tallies; guidance to redirect to files) may capture most of the win without annoying blocking. → Check native: does microcompaction already clear old tool results in v2.1.x? If yes, prevention matters less; visibility still matters.

H6. **Recovery must be automatic.** SessionStart(compact|clear|resume) hooks can re-inject the ledger so the post-compact agent re-anchors without user action. → Verify SessionStart matchers and injection size limits.

## Sketch (pre-research, likely wrong in places)

- **Hooks (python3 stdlib or bash, no deps):**
  - context-watch: on PostToolUse (sampled) or UserPromptSubmit — compute context % from transcript usage fields; at thresholds (~60/75/85%) inject escalating advisories with concrete suggested actions; include a per-tool-result "top offenders" tally so cleanup is targeted.
  - precompact-snapshot: on PreCompact — save full transcript copy + stats to .claude/context-snapshots/; on auto-compact, warn (post-hoc) that an unplanned compact happened so the agent re-anchors from the ledger.
  - session-rehydrate: on SessionStart(compact|clear|resume) — inject ledger summary + "re-read ledger before continuing" instruction.
- **Skill:** `context-ledger` — the discipline: when to write the ledger (task start, decisions, before risky ops, at thresholds), strict format, what NOT to put in it. Model-invoked by judgement + nudged by hooks.
- **Commands:** `/ledger` (write/update now), `/handoff` (write ledger + emit compact-with-instructions or clear-and-rehydrate recipe), maybe `/context-status` (on-demand report).
- **Statusline:** Jonathan already has one — at most, offer an optional context-% segment; don't fight it. Possibly skip.

## Design principles

1. Lean simple: fewest moving parts that produce the behavior. No daemons, no DBs, no API calls from hooks (hooks must be fast + free), stdlib only.
2. Advisory over blocking: never deny tools; inject signal, let the agent judge. (Possible exception: warn-once before catastrophically large single results.)
3. Native-first: complement v2.1.x native features (microcompact, /rewind, auto-memory); never duplicate or fight them.
4. Files are the memory: everything the plugin persists is human-readable markdown/JSON in the project (or ~/.claude for global), reviewable by Jonathan.
5. Fail open: hook errors must never break the session (timeouts, malformed transcript → exit 0 silently, log to a debug file).

## Open questions for research

- Exact injection-capable hook events + size etiquette; UserPromptSubmit vs PostToolUse for the watch hook (frequency vs staleness trade-off).
- Does v2.1.x microcompaction already clear old tool results? Threshold? Configurable? (If yes, H5 shifts to visibility-only.)
- Can the model invoke /compact or /clear itself via SlashCommand tool? Can PreCompact hooks shape the compaction summary (custom_instructions)?
- Auto-compact threshold + whether plugins can read "context left" directly from any hook payload vs computing from transcript.
- Transcript JSONL usage-field anatomy on 2.1.241 (cache-aware token math).
- What claude-mem & co. already solved that we should borrow (and their failure modes: latency, cost, injection bloat).
- Whether "Wet" exists and what it got right.
