---
name: context-ledger
description: Keep the dry task ledger — deterministic working memory that survives compaction and /clear — and handle compaction the right way for the run you are in (gated self-compaction vs. user-run /compact). Use when starting a task expected to run long or touch many files; the moment any "[dry]" advisory or pending-compaction notice appears; when a subtask closes or a natural boundary is reached; before any compaction or /clear; and when resuming work after a compaction, clear, or resume.
---

# context-ledger

You are keeping a ledger so that compaction, /clear, and context rot cannot take your working state from you. The ledger, not the transcript, is your durable memory. Everything else in the dry plugin exists to snapshot, re-inject, and point back at this file.

## First: which kind of run is this?

- **Gated run (self-compaction).** Launched with `DRY_GATE=1` (for example the `autoclaude` shortcut). dry's advisories say "Gated run". dry holds the harness's auto-compact back until *you* release it, so **compaction is yours**: nobody will run /compact for you, and you must not ask them to. You compact by releasing the gate at a boundary you choose — see the playbook below.
- **Interactive run.** No gate. The user runs /compact or /clear; you cannot. Keep the ledger current and, at a clean boundary, offer a one-line suggestion.

If unsure: `test -n "$DRY_GATE" && echo gated || echo interactive`.

## The ledger file

The ledger lives at `<project>/.claude/dry/ledger.md`. If it does not exist when you start a long task, create it (make the directory if needed) with exactly this template:

```markdown
# Ledger — <one-line task name>

## Goal
<one or two lines: what done looks like>

## Now
<the single subtask currently in progress>

## Done
- <utc timestamp> <one-liner>

## Decisions
- D1. <what was decided> — because <why>

## Files
- <path> — <one clause: why this file matters to the task>

## Gotchas
- <trap, invariant, or surprising fact that would burn a fresh agent>

## Next
1. <next concrete step>
2. <the one after>
```

## Discipline: itemized deltas, never full rewrites

Update the ledger by adding, editing, or folding individual lines. Never regenerate the whole file from memory: full rewrites cause context collapse — brevity bias eats exactly the detail that matters, and each rewrite compounds the loss.

- **Done** is append-only one-liners. When a subtask closes, fold all Done lines older than the current subtask into a single summary line.
- **Decisions** record what AND why. The why is what compaction loses; a decision without its reason will be re-litigated by your post-compaction self. Number them (D1, D2, …) so they can be cited.
- **Files** map path → one clause on why it matters. Not contents — paths.
- **Gotchas** hold anything that already cost you time once.
- **Now** and **Next** are always current — they are the first things read after a reset.
- Keep the whole file at or under ~150 lines. If it grows past that, fold Done and prune Files; never thin out Decisions or Gotchas.

## Recitation

After every ledger update, re-read the `## Now` and `## Next` sections aloud in your working notes — literally restate them in your reply or thinking. Fresh restatement at the tail of context re-anchors attention on the plan (the todo.md effect); mid-context material is functionally faded.

## When to update

- **Task start** — create the ledger before the first heavy exploration.
- **Each decision made** — one Decisions line, immediately, with the why.
- **Each subtask closed** — Done line, fold older Done lines, refresh Now/Next.
- **Before risky operations** — migrations, force-pushes, large refactors, anything hard to undo.
- **Whenever a [dry] advisory fires** — see the playbooks below.
- **Before any compaction** — releasing a gated one, or a user-run /compact or /clear. The ledger is what makes the reset survivable.

## Responding to [dry] advisories

dry measures your context against a *working budget* (200k tokens by default) and injects one advisory per band crossing (50%, 70%, 85%). The budget is a hygiene reference: attention quality degrades well before it, and it is the minimum at which a compaction pays off. It is not the model's window and not a deadline. A downward jump in usage means a compaction happened; the bands re-arm automatically.

### Gated run playbook

Stance: do not worry about your compaction state. dry exists so you can declutter your own context when you can, without a round-trip to the user.

- **50%** — update the ledger. Shift heavy reads and broad exploration to subagents (they return conclusions, not transcripts); divert big command outputs to files. Nothing to raise with the user.
- **70%** — finish the current subtask before opening new exploration; bring the ledger current. Nothing to raise with the user.
- **85%** — keep the ledger current and tool output lean; carry on working. Nothing to raise with the user.
- **Pending notice** — `[dry] A gated auto-compact is pending …` is the cue, and the *only* moment compaction is actually available. Finish the current step, bring the ledger current, then release with one Bash call: `touch .claude/dry/compact-ok` (the notice gives the absolute path). Compaction proceeds on the harness's next attempt, usually the next turn, and dry rehydrates you from the ledger. If the notice lands mid-step, keep going and release at the next boundary; dry reminds you every 30 minutes while it waits.

Never, in a gated run:

- propose /compact or /clear to the user, or ask whether to compact;
- report context percentages, "compaction point", or "context is getting full" in user-facing messages — the user has their own view of that;
- touch compact-ok before a pending notice — the flag does nothing until the harness attempts a compaction, and it goes stale after 60 minutes;
- release mid-subtask "to be safe" — the pending compaction waits for you;
- touch compact-ok from inside a subagent — any [dry] notice a subagent sees describes its *parent's* context (dry stays silent in subagents, but the rule stands).

If you never release, dry takes the compaction itself once it has been pending for 90 minutes (`gate_max_pending_minutes`), and a failsafe compacts near the model's real window; nothing can strand the session. One exception to the silence rule: if dry tells you a compaction was *forced* (failsafe or timeout), a single line to the user about it is appropriate.

### Interactive run playbook

- **50%** — update the ledger; shift heavy reads to subagents; divert big outputs to files.
- **70%** — finish the current subtask, update the ledger, and when you next report, note in one line that this is a good /compact (or /clear) boundary, with ledger-derived keep instructions.
- **85%** — checkpoint now (ledger first, minimal further tool output) and include that one line in your next user-facing message, e.g. `/compact keep decisions D1–D4, the file map, gotchas; drop exploration transcripts.`

You have no tool that runs /compact (the Skill tool exposes only a few built-ins, and /compact is not among them). One line is enough; the user decides. dry re-injects the ledger after /compact and a pointer to it after /clear.

## After a compaction, /clear, or resume

dry re-injects the ledger (compact) or a pointer to it (clear/resume). Read `## Now` and `## Next` first. When the ledger and the native compaction summary disagree, trust the ledger — it was written deliberately, at boundaries, by a sharp model; the summary was not. The injected preamble says what kind of reset this was: your own boundary release (normal — carry on from Now/Next), or one forced by the gate's timeout or failsafe (ungraceful — re-read Now/Next with extra care and note in Gotchas what was mid-flight). The same fact lands in the ledger afterwards as a one-line `✓` / `⚠` / `ℹ` bookkeeping entry; dry keeps only the newest of those when it re-injects the ledger.

## Archive awareness

Oversized tool results may have been diverted to disk before they ever reached you. Stubs say `[dry diverted ...]` and give a path. When you need the missing middle, Read or Grep the archive (`.claude/dry/archive/` or the exact path in the stub) — do NOT re-run the command; the full original is already on disk.

## Where dry looks

dry keeps `.claude/dry/` (ledger, snapshots, gate flag, pending marker) under the **project root** — the directory the session started in (`CLAUDE_PROJECT_DIR`) — not under whatever directory your shell has `cd`'d into. Use the absolute paths dry gives you in notices and in `/dry:status`; when you edit the ledger by hand, use the project-root path. (A `compact-ok` touched relative to a drifted cwd is still honoured, but don't rely on it.)

## Do NOT put in the ledger

- Anything already in CLAUDE.md — it is re-read every session anyway.
- Code that lives in files — reference the path in `## Files` instead of quoting bodies.
- Secrets, tokens, or credentials of any kind.
