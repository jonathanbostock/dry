#!/usr/bin/env python3
"""RESET leg, write side: checkpoint around native compaction.

Wired to BOTH PreCompact and PostCompact (see hooks/hooks.json); branches on
hook_event_name.

  PreCompact  — stream-gzip the full transcript to
                <cwd>/.claude/dry/snapshots/<utc>-<sid8>-<trigger>.jsonl.gz,
                prune old snapshots, and on trigger=="auto" append a warning
                line to the ledger (the post-compact agent sees it via
                dry_rehydrate).
  PostCompact — save compact_summary verbatim to
                snapshots/<utc>-<sid8>-summary-<trigger>.md as an audit trail
                of what native compaction actually kept; prune old summaries.

Side effects only: never writes stdout, never blocks compaction, always
exits 0 (fail_open).
"""

from __future__ import annotations

import gzip
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from _common import (
    atomic_write,
    fail_open,
    load_config,
    log_debug,
    now_stamp,
    project_dry_dir,
    read_stdin_json,
)


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


def append_ledger_warning(dry_dir: Path, cwd: str, snapshot: Path) -> None:
    try:
        rel = os.path.relpath(snapshot, cwd)
    except ValueError:
        rel = str(snapshot)
    ledger = dry_dir / "ledger.md"
    header = "" if ledger.exists() else "# dry ledger\n\n"
    entry = f"\n- ⚠ {utc_iso()}: auto-compact fired mid-task; pre-compact snapshot: {rel}\n"
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(header + entry)


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
    dest = snapdir / f"{now_stamp()}-{sid8(data.get('session_id'))}-{safe_name(trigger)}.jsonl.gz"
    if not snapshot_transcript(transcript_path, dest):
        return
    prune(snapdir, "*.jsonl.gz", cfg["snapshots_keep"])
    if trigger == "auto":
        append_ledger_warning(dry_dir, cwd, dest)


def handle_post_compact(data: dict, cfg: dict) -> None:
    summary = data.get("compact_summary")
    if not isinstance(summary, str) or not summary:
        return
    cwd = data["cwd"]
    trigger = data.get("trigger") or "unknown"
    snapdir = project_dry_dir(cwd, create=True) / "snapshots"
    dest = snapdir / f"{now_stamp()}-{sid8(data.get('session_id'))}-summary-{safe_name(trigger)}.md"
    atomic_write(dest, f"saved: {utc_iso()}\ntrigger: {trigger}\n\n{summary}")
    prune(snapdir, "*.md", cfg["snapshots_keep"])


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
