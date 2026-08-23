---
description: Checkpoint the task ledger and recommend the cleanest context reset
argument-hint: [focus]
allowed-tools: Bash(python3:*)
---

<!-- ${CLAUDE_PLUGIN_ROOT} is documented as resolving anywhere in skill/command content
     (research/hooks-api.md §1.3), but its availability inside `!` preprocessing lines
     specifically is not stated verbatim. If the line below fails to resolve, fall back to
     invoking the script by absolute path, e.g.:
       python3 /workspace/claude-memory-management/scripts/dry_status.py
     (or the installed plugin path shown by `claude plugin details dry`). -->

Checkpoint the task and recommend the cleanest context reset. Focus, if given: **$ARGUMENTS**

## Step 1 — bring the ledger current

Invoke and follow the context-ledger skill (`dry:context-ledger`) to bring `<cwd>/.claude/dry/ledger.md` fully up to date: fold Done lines, record any un-recorded decisions with their whys, refresh Now/Next, and note gotchas. If `$ARGUMENTS` names a focus, make that the `## Now` and shape `## Next` around it. Update by itemized deltas, never a full rewrite.

## Step 2 — see where context stands

The dry status report, captured at invocation time:

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dry_status.py" --session "${CLAUDE_SESSION_ID}"`

## Step 3 — recommendation block

End your reply with a clearly-marked recommendation block for the user containing exactly one of:

- `/compact <specific keep/drop instructions derived from the ledger>` — when there is more of THIS task to do and we are at or past a band at a clean boundary. Cite ledger items by name (e.g. "keep decisions D1–D3, the file map, gotchas; drop exploration transcripts").
- `/clear` — when the current task is done or the next piece of work is unrelated. dry automatically injects a pointer to the ledger in the fresh session, so nothing is lost.
- **keep going** — when usage is below the first band; a reset now would cost more (cache, momentum) than it saves.

Follow the recommendation with one sentence of why, grounded in the report and the ledger state.
