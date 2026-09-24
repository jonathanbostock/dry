#!/usr/bin/env python3
"""RESET leg, read side: re-anchor the agent after compact/clear/resume.

SessionStart hook, matcher compact|clear|resume (see hooks/hooks.json).

  source == "compact"       — inject the task ledger (capped at 6k chars)
                              plus a pointer to the newest pre-compact
                              snapshot and, when dry_gate left a
                              compact-released marker, one sentence on what
                              kind of reset this was (own boundary release /
                              failsafe / timeout / blind). This hook runs
                              BEFORE PostCompact, so it reads the marker
                              without consuming it and clears any stale
                              compact-pending marker. If no ledger exists,
                              tell the agent to create one.
  source clear|resume       — inject a one-line pointer to a recent ledger
                              (goal + age); silent when the ledger is missing
                              or older than ledger_pointer_max_age_days.

Injected text is plugin-generated phrasing plus content of dry's own files
(ledger head, goal line, snapshot path). Always exits 0; silent on anomaly.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _common import (
        cap_text,
        emit,
        fail_open,
        load_config,
        project_dry_dir,
        read_stdin_json,
    )
except Exception:  # import failure must never break the session
    sys.exit(0)

LEDGER_READ_MAX = 64 * 1024
LEDGER_CAP = 6_000
GOAL_CAP = 150
TRUNCATION_MARKER = "\n[dry: ledger truncated — Read .claude/dry/ledger.md for the rest]"

# Bookkeeping lines dry itself appends to the ledger; only the newest one is
# worth injecting (old runs accumulated hundreds and crowded out Goal/Now).
BOOKKEEPING_MARKERS = (
    "auto-compact fired mid-task",
    "compaction released at a boundary",
    "forced by the gate's failsafe",
    "taken automatically after the gate",
    "auto-compact allowed (dry could not",
)

RESET_NOTES = {
    "flag": ("This compaction was your own boundary release (gated run) — a normal"
             " reset; pick up from Now/Next."),
    "failsafe": ("This compaction was forced by the gate's failsafe near the model's"
                 " window limit — the gate was never released, so treat the reset as"
                 " ungraceful: anything mid-flight may be missing from the ledger."),
    "timeout": ("This compaction was taken automatically after the gate had been"
                " pending {detail} without release — treat the reset as ungraceful:"
                " anything mid-flight may be missing from the ledger."),
    "blind": ("dry could not read the context size and let this auto-compact"
              " through; it was not at a boundary you chose."),
}

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


def read_head(path: Path) -> str:
    """Bounded read: the caps come later, but never load a planted-huge file whole."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(LEDGER_READ_MAX)


def prune_bookkeeping(text: str) -> str:
    """Keep only the newest dry bookkeeping line; squeeze the blank runs left behind."""
    lines = text.splitlines()
    hits = [
        i for i, line in enumerate(lines)
        if line.lstrip().startswith("- ") and any(m in line for m in BOOKKEEPING_MARKERS)
    ]
    if len(hits) <= 1:
        return text
    drop = set(hits[:-1])
    out: list[str] = []
    for i, line in enumerate(lines):
        if i in drop:
            continue
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def released_note(dry_dir: Path) -> str:
    """Read (never consume — PostCompact does that) dry_gate's release marker."""
    try:
        parts = (dry_dir / "compact-released").read_text(encoding="utf-8").split()
    except OSError:
        return ""
    if not parts or parts[0] not in RESET_NOTES:
        return ""
    detail = f"~{parts[1]} min" if len(parts) > 1 else "a long time"
    return " " + RESET_NOTES[parts[0]].format(detail=detail)


def compact_context(cwd: str, ledger: Path) -> str:
    dry_dir = project_dry_dir(cwd)
    if not ledger.is_file():
        return NO_LEDGER_MSG + released_note(dry_dir)
    text = cap_text(prune_bookkeeping(read_head(ledger)), LEDGER_CAP, TRUNCATION_MARKER)
    snap = newest_snapshot(dry_dir / "snapshots")
    snapnote = ""
    if snap is not None:
        snapnote = f" (full pre-compact transcript snapshot: {rel_to(snap, cwd)})"
    return (
        "[dry] Context was just compacted. The native summary above may have "
        f"dropped specifics.{released_note(dry_dir)} The task ledger below is "
        f"authoritative{snapnote}. Re-read it before continuing.\n\n---\n{text}\n---"
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
    goal = cap_text(extract_goal(read_head(ledger)), GOAL_CAP, "…")
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
        # Whatever was pending is over now (a manual /compact never clears it).
        (project_dry_dir(data["cwd"]) / "compact-pending").unlink(missing_ok=True)
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
