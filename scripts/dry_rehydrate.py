#!/usr/bin/env python3
"""RESET leg, read side: re-anchor the agent after compact/clear/resume.

SessionStart hook, matcher compact|clear|resume (see hooks/hooks.json).

  source == "compact"       — inject the task ledger (capped at 6k chars)
                              plus a pointer to the newest pre-compact
                              snapshot; if no ledger exists, tell the agent
                              to create one.
  source clear|resume       — inject a one-line pointer to a recent ledger
                              (goal + age); silent when the ledger is missing
                              or older than ledger_pointer_max_age_days.

Injected text is plugin-generated phrasing plus content of dry's own files
(ledger head, goal line, snapshot path). Always exits 0; silent on anomaly.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from _common import (
    cap_text,
    emit,
    fail_open,
    load_config,
    project_dry_dir,
    read_stdin_json,
)

LEDGER_CAP = 6_000
GOAL_CAP = 150
TRUNCATION_MARKER = "\n[dry: ledger truncated — Read .claude/dry/ledger.md for the rest]"

NO_LEDGER_MSG = (
    "[dry] Context was just compacted and no task ledger exists at "
    ".claude/dry/ledger.md. If this task continues, create one now "
    "(context-ledger skill) so the next compaction is lossless."
)


def newest_snapshot(snapdir: Path) -> Path | None:
    if not snapdir.is_dir():
        return None
    snaps = sorted(p for p in snapdir.glob("*.jsonl.gz") if p.is_file())
    return snaps[-1] if snaps else None


def rel_to(path: Path, cwd: str) -> str:
    try:
        return os.path.relpath(path, cwd)
    except ValueError:
        return str(path)


def compact_context(cwd: str, ledger: Path) -> str:
    if not ledger.is_file():
        return NO_LEDGER_MSG
    text = cap_text(
        ledger.read_text(encoding="utf-8", errors="replace"),
        LEDGER_CAP,
        TRUNCATION_MARKER,
    )
    snap = newest_snapshot(project_dry_dir(cwd) / "snapshots")
    snapnote = ""
    if snap is not None:
        snapnote = f" (full pre-compact transcript snapshot: {rel_to(snap, cwd)})"
    return (
        "[dry] Context was just compacted. The native summary above may have "
        f"dropped specifics. The task ledger below is authoritative{snapnote}. "
        f"Re-read it before continuing.\n\n---\n{text}\n---"
    )


def extract_goal(text: str) -> str:
    """First non-empty line under '## Goal'; else first non-heading line."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## goal"):
            for follow in lines[i + 1 :]:
                stripped = follow.strip()
                if stripped.startswith("#"):
                    break
                if stripped:
                    return stripped
            break
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return "untitled"


def humanize_age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def pointer_context(cfg: dict, ledger: Path) -> str | None:
    if not ledger.is_file():
        return None
    age = time.time() - ledger.stat().st_mtime
    if age >= cfg["ledger_pointer_max_age_days"] * 86400:
        return None
    goal = cap_text(
        extract_goal(ledger.read_text(encoding="utf-8", errors="replace")),
        GOAL_CAP,
        "…",
    )
    return (
        f"[dry] A task ledger from {humanize_age(age)} exists at "
        f".claude/dry/ledger.md (goal: {goal}). Read it first if you are "
        "resuming that work; ignore it for unrelated tasks."
    )


def main() -> None:
    data = read_stdin_json()
    cfg = load_config(data["cwd"])
    if cfg["disable"] or cfg["disable_rehydrate"]:
        return
    source = data.get("source")
    ledger = project_dry_dir(data["cwd"]) / "ledger.md"
    if source == "compact":
        text = compact_context(data["cwd"], ledger)
    elif source in ("clear", "resume"):
        text = pointer_context(cfg, ledger)
    else:
        return
    if not text:
        return
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": text,
            }
        }
    )


if __name__ == "__main__":
    fail_open(main)
