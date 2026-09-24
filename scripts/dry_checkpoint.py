#!/usr/bin/env python3
"""RESET leg, write side: checkpoint around native compaction.

Wired to BOTH PreCompact and PostCompact (see hooks/hooks.json); branches on
hook_event_name.

  PreCompact  — stream-gzip the full transcript to
                <cwd>/.claude/dry/snapshots/<utc>-<sid8>-<trigger>.jsonl.gz
                and prune old snapshots. Nothing else: in gated runs this
                event also fires on every *blocked* attempt, so it must not
                write anything that reads as "a compaction happened".
  PostCompact — a compaction actually happened. Save compact_summary
                verbatim to snapshots/<utc>-<sid8>-summary-<trigger>.md as an
                audit trail, prune old summaries, and on trigger=="auto"
                append ONE ledger line (the post-compact agent sees it via
                dry_rehydrate): a calm "released at a boundary" note when the
                gate's compact-released marker says the agent released it, a
                "forced by the failsafe" warning when the marker says failsafe,
                otherwise the plain "auto-compact fired mid-task" warning.

Side effects only: never writes stdout, never blocks compaction, always
exits 0 (fail_open).
"""

from __future__ import annotations

import gzip
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _common import (
        atomic_write,
        fail_open,
        gate_enabled,
        load_config,
        log_debug,
        now_stamp,
        project_dry_dir,
        read_stdin_json,
    )
except Exception:  # import failure must never break the session
    sys.exit(0)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(part: object) -> str:
    """Same sanitization as _common.state_dir: alnum, '-', '_' only."""
    return "".join(c for c in str(part) if c.isalnum() or c in "-_") or "unknown"


def sid8(session_id: object) -> str:
    return safe_name(session_id)[:8]


def prune(snapdir: Path, pattern: str, keep: int) -> None:
    """Unlink the oldest (name-sorted) matches so at most `keep` remain."""
    if not snapdir.is_dir():
        return
    files = sorted(p for p in snapdir.glob(pattern) if p.is_file())
    excess = len(files) - max(0, int(keep))
    for path in files[: max(0, excess)]:
        try:
            path.unlink()
        except OSError:
            log_debug(f"prune: could not unlink {path}")


def snapshot_transcript(transcript_path: str, dest: Path) -> bool:
    """Stream-gzip the transcript to dest (never load it whole)."""
    tmp = dest.with_name(dest.name + f".tmp{os.getpid()}")
    try:
        with open(transcript_path, "rb") as src, gzip.open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp, dest)
        return True
    except OSError:
        log_debug(f"snapshot of {transcript_path} failed")
        tmp.unlink(missing_ok=True)
        return False


RELEASED_MARKER_MAX_AGE_SECONDS = 120  # PreCompact -> PostCompact is seconds apart


def append_ledger_line(dry_dir: Path, entry: str) -> None:
    ledger = dry_dir / "ledger.md"
    header = "" if ledger.exists() else "# dry ledger\n\n"
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(header + "\n" + entry + "\n")


RELEASE_KINDS = ("flag", "failsafe", "timeout", "blind")


def consume_released_marker(dry_dir: Path) -> tuple[str, str] | None:
    """Return (kind, detail) from a fresh dry_gate marker, removing it."""
    marker = dry_dir / "compact-released"
    try:
        age = time.time() - marker.stat().st_mtime
        parts = marker.read_text(encoding="utf-8").split()
    except OSError:
        return None
    marker.unlink(missing_ok=True)
    if age > RELEASED_MARKER_MAX_AGE_SECONDS or not parts or parts[0] not in RELEASE_KINDS:
        return None
    return parts[0], (parts[1] if len(parts) > 1 else "")


def newest_snapshot(snapdir: Path) -> Path | None:
    if not snapdir.is_dir():
        return None
    snaps = [p for p in snapdir.glob("*.jsonl.gz") if p.is_file()]
    return max(snaps, key=lambda p: p.stat().st_mtime) if snaps else None


def ledger_note_after_auto_compact(dry_dir: Path, cwd: str) -> None:
    """One line so the rehydrated agent knows what kind of reset just happened."""
    released = consume_released_marker(dry_dir)
    kind, detail = released if released else ("", "")
    tail = ""
    snap = newest_snapshot(dry_dir / "snapshots")
    if snap is not None:
        try:
            rel = os.path.relpath(snap, cwd)
        except ValueError:
            rel = str(snap)
        tail = f"; pre-compact snapshot: {rel}"
    stamp = utc_iso()
    if kind == "flag":
        entry = f"- ✓ {stamp}: compaction released at a boundary (gated run){tail}"
    elif kind == "failsafe":
        entry = f"- ⚠ {stamp}: auto-compact forced by the gate's failsafe (never released){tail}"
    elif kind == "timeout":
        mins = f" {detail} min" if detail else ""
        entry = f"- ⚠ {stamp}: compaction taken automatically after the gate had been pending{mins} without release{tail}"
    elif kind == "blind":
        entry = f"- ℹ {stamp}: auto-compact allowed (dry could not read the context size){tail}"
    else:
        entry = f"- ⚠ {stamp}: auto-compact fired mid-task{tail}"
    append_ledger_line(dry_dir, entry)


def handle_pre_compact(data: dict, cfg: dict) -> None:
    cwd = data["cwd"]
    trigger = data.get("trigger") or "unknown"
    dry_dir = project_dry_dir(cwd, create=True)
    transcript_path = data.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        log_debug("PreCompact: no usable transcript_path; skipping snapshot")
        return
    snapdir = dry_dir / "snapshots"
    snapdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Gated runs retry PreCompact every ~80 s while blocked; don't re-gzip a
    # large transcript on each attempt (the ledger, not the snapshot, is the
    # recovery anchor — a snapshot up to 10 min old is plenty).
    interval = (
        cfg["gate_snapshot_min_interval_seconds"] if gate_enabled(cfg)
        else cfg["snapshot_min_interval_seconds"]
    )
    newest = max((p.stat().st_mtime for p in snapdir.glob("*.jsonl.gz")), default=0.0)
    if time.time() - newest < interval:
        return
    dest = snapdir / f"{now_stamp()}-{sid8(data.get('session_id'))}-{safe_name(trigger)}.jsonl.gz"
    if not snapshot_transcript(transcript_path, dest):
        return
    prune(snapdir, "*.jsonl.gz", cfg["snapshots_keep"])


def handle_post_compact(data: dict, cfg: dict) -> None:
    cwd = data["cwd"]
    trigger = data.get("trigger") or "unknown"
    dry_dir = project_dry_dir(cwd, create=True)
    summary = data.get("compact_summary")
    if isinstance(summary, str) and summary:
        snapdir = dry_dir / "snapshots"
        dest = snapdir / f"{now_stamp()}-{sid8(data.get('session_id'))}-summary-{safe_name(trigger)}.md"
        atomic_write(dest, f"saved: {utc_iso()}\ntrigger: {trigger}\n\n{summary}")
        prune(snapdir, "*.md", cfg["snapshots_keep"])
    if trigger == "auto":
        ledger_note_after_auto_compact(dry_dir, cwd)


def main() -> None:
    data = read_stdin_json()
    cfg = load_config(data["cwd"])
    if cfg["disable"] or cfg["disable_checkpoint"]:
        return
    event = data.get("hook_event_name")
    if event == "PreCompact":
        handle_pre_compact(data, cfg)
    elif event == "PostCompact":
        handle_post_compact(data, cfg)


if __name__ == "__main__":
    fail_open(main)
