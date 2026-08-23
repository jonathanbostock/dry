---
name: context-ledger
description: Keep the dry task ledger — deterministic working memory that survives compaction and /clear. Use when starting a task expected to run long or touch many files; the moment any "[dry]" context advisory appears in the conversation; when a subtask closes or a natural boundary is reached; before recommending /compact or /clear to the user; and when resuming work after a compaction, clear, or resume.
---

# context-ledger

You are keeping a ledger so that compaction, /clear, and context rot cannot take your working state from you. The ledger, not the transcript, is your durable memory. Maintain it as described below; everything else in the dry plugin exists to snapshot, re-inject, and point back at this file.

## The ledger file

The ledger lives at `<cwd>/.claude/dry/ledger.md`. If it does not exist when you start a long task, create it (make the directory if needed) with exactly this template:

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
- **Whenever a [dry] advisory fires** — see the band playbook below.
- **Before recommending compaction** — the ledger is what makes the compaction survivable.

## Responding to [dry] advisories (band playbook)

dry injects an advisory once per band crossing. Treat each as an interrupt with a fixed response:

- **50%** — update the ledger now. Then change how you work: shift heavy reads and broad exploration to subagents (return conclusions, not transcripts), and divert big command outputs to files instead of context (`> file` then Read selectively).
- **70%** — finish the current subtask (do not start a new one), update the ledger, then recommend a boundary compaction to the user. Auto-compact will not be graceful; a judged boundary compaction will be.
- **85%** — checkpoint immediately: update the ledger before anything else, keep further output minimal, and propose compaction in your very next user-facing message.

A downward jump in usage means a compaction happened; bands re-arm automatically.

## Compaction protocol

You CANNOT run /compact yourself — the Skill tool excludes built-in commands (verified platform limitation). Instead:

1. Bring the ledger fully current.
2. Propose compaction to the user, with keep/drop instructions derived from the ledger. For example:

   > Good boundary to compact. Suggest: `/compact keep decisions D1–D4, the file map, gotchas; drop exploration transcripts.`

3. After any compaction, dry re-injects the ledger automatically. When the ledger and the native compaction summary disagree, trust the ledger — it was written deliberately, at boundaries, by a sharp model; the summary was not.

The same holds for /clear: recommend it only after a ledger update; dry injects a pointer to the ledger in the fresh session.

## Archive awareness

Oversized tool results may have been diverted to disk before they ever reached you. Stubs say `[dry diverted ...]` and give a path. When you need the missing middle, Read or Grep the archive (`.claude/dry/archive/` or the exact path in the stub) — do NOT re-run the command; the full original is already on disk.

## Do NOT put in the ledger

- Anything already in CLAUDE.md — it is re-read every session anyway.
- Code that lives in files — reference the path in `## Files` instead of quoting bodies.
- Secrets, tokens, or credentials of any kind.
