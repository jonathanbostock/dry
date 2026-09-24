---
description: Checkpoint the task ledger, then reset context the right way for this run — release the gate (gated runs) or recommend /compact or /clear (interactive runs)
argument-hint: [focus]
allowed-tools: Bash(python3:*), Bash(touch:*)
---

<!-- If ${CLAUDE_PLUGIN_ROOT} ever fails to resolve in the `!` line below, invoke
     dry_status.py by the installed path shown in `claude plugin details dry`. -->

Checkpoint the task and hand off cleanly. Focus, if given: **$ARGUMENTS**

## Step 1 — bring the ledger current

Invoke and follow the context-ledger skill (`dry:context-ledger`) to bring `<project>/.claude/dry/ledger.md` fully up to date: fold Done lines, record any un-recorded decisions with their whys, refresh Now/Next, and note gotchas. If `$ARGUMENTS` names a focus, make that the `## Now` and shape `## Next` around it. Update by itemized deltas, never a full rewrite.

## Step 2 — see where context stands

The dry status report, captured at invocation time:

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dry_status.py" --session "${CLAUDE_SESSION_ID}"`

## Step 3 — act according to the `mode:` line

**mode: gated self-compaction** — the reset is yours to perform; do not ask the user.

- If `gate:` shows an auto-compact pending: this command *is* your boundary, so release it now with the `touch` command printed on that line, and say in one line that the ledger is checkpointed and compaction proceeds on the next attempt. Then continue with the next ledger item.
- If nothing is pending: say in one line that the ledger is checkpointed, and carry on. Compaction becomes available when the harness next attempts one; dry will tell you.

**mode: interactive** — end your reply with a clearly marked recommendation block containing exactly one of:

- `/compact <specific keep/drop instructions derived from the ledger>` — when there is more of THIS task to do and we are at or past a band at a clean boundary. Cite ledger items by name (e.g. "keep decisions D1–D3, the file map, gotchas; drop exploration transcripts").
- `/clear` — when the current task is done or the next piece of work is unrelated. dry automatically injects a pointer to the ledger in the fresh session, so nothing is lost.
- **keep going** — when usage is below the first band; a reset now would cost more (cache, momentum) than it saves.

Follow the recommendation with one sentence of why, grounded in the report and the ledger state.
