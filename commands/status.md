---
description: Report current context usage, top result hogs, ledger and snapshot state, and a suggested next action
allowed-tools: Bash(python3:*)
---

<!-- ${CLAUDE_PLUGIN_ROOT} is documented as resolving anywhere in skill/command content
     (research/hooks-api.md §1.3), but its availability inside `!` preprocessing lines
     specifically is not stated verbatim. If the line below fails to resolve, fall back to
     invoking the script by absolute path, e.g.:
       python3 /workspace/claude-memory-management/scripts/dry_status.py
     (or the installed plugin path shown by `claude plugin details dry`). -->

## dry status report

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dry_status.py"`

## Your task

Interpret the report above for the user in 2–3 sentences: current context usage against the reference window, what is eating it (top result hogs), and the health of the ledger and snapshots.

If a band is active, take or propose the band-appropriate action per the context-ledger skill (`dry:context-ledger`): at 50%, update the ledger and shift heavy work off the main thread; at 70%, finish the current subtask, update the ledger, and recommend a boundary compaction; at 85%, checkpoint immediately and propose `/compact` with ledger-derived keep/drop instructions. If no band is active, say so and carry on — no action needed.
