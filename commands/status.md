---
description: Report current context usage, run mode (gated self-compaction vs interactive), gate state, top result hogs, ledger and snapshot state, and a suggested next action
allowed-tools: Bash(python3:*)
---

<!-- If ${CLAUDE_PLUGIN_ROOT} ever fails to resolve in the `!` line below, invoke
     dry_status.py by the installed path shown in `claude plugin details dry`. -->

## dry status report

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dry_status.py" --session "${CLAUDE_SESSION_ID}"`

## Your task

Interpret the report above in 2–3 sentences: context usage against the working budget, what is eating it (top hogs), and the health of the ledger and snapshots. Then act according to the `mode:` line and the context-ledger skill (`dry:context-ledger`):

- **mode: gated self-compaction** — compaction is yours, not the user's. If `gate:` shows an auto-compact pending and you are at a clean boundary, bring the ledger current and release it with the `touch` command printed on that line. Otherwise keep working: update the ledger at 50%, finish the current subtask at 70%, stay lean at 85%. Do not propose /compact or /clear to the user and do not dwell on percentages.
- **mode: interactive** — at 50%, update the ledger and shift heavy work off the main thread; at 70%, finish the current subtask, update the ledger, and note a good /compact boundary in one line; at 85%, checkpoint now and suggest `/compact` with ledger-derived keep/drop instructions. If no band is active, say so and carry on.
