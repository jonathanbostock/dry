#!/usr/bin/env python3
"""Self-compaction gate (OPT-IN): defer proactive auto-compacts to a boundary.

PreCompact hook. The model cannot trigger /compact, but a PreCompact hook CAN
skip a proactive auto-compact (exit 2), and blocked attempts retry — verified
live on v2.1.241 (2026-08-23): five PreCompact(auto) attempts in one session,
with compaction proceeding on the first allowed one. That inverts control:
run with a low --autocompact window so a compaction is effectively always
pending past the threshold, and let the AGENT release it at a clean boundary.

Protocol:
  - Disabled by default. Enable with DRY_GATE=1 (or DRY_GATE_ENABLED=1, or
    config gate_enabled=true). Intended for autonomous runs, e.g.:
      DRY_GATE=1 claude --autocompact 220000   # harness attempts at ~90% of N, i.e. ~200k
  - trigger "manual": always allowed — the user asked for it.
  - trigger "auto" + fresh <cwd>/.claude/dry/compact-ok flag: allow and
    consume the flag (one-shot). The agent creates it at a boundary:
      touch .claude/dry/compact-ok
  - trigger "auto", no flag: block (exit 2) and leave a compact-pending
    marker, which dry_watch turns into an in-context notice so the agent
    knows to wrap up and release.
  - On every allow (flag or failsafe) the gate leaves <cwd>/.claude/dry/
    compact-released ("flag" | "failsafe") so dry_checkpoint's PostCompact
    ledger note can say whether the compaction was the agent's own boundary
    release or a forced one.
  - Failsafe: near the model's real window the gate always allows — blocking
    an error-recovery compaction would fail the request outright. A blind
    gate (transcript unreadable) also allows. And once an attempt has been
    pending for gate_max_pending_minutes (default 90) the gate lets the next
    one through: a lost notice must never strand a run until the failsafe.
  - A manual /compact clears any pending episode, so the next auto attempt
    re-notifies instead of sitting silently behind an already-seen marker.
  - Markers live under the project root (CLAUDE_PROJECT_DIR), but a flag
    touched relative to a drifted shell cwd is honoured too.

Fail-open direction is ALLOW: any internal error -> exit 0 (never strand a
session behind a broken gate).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _common import (
        atomic_write,
        fail_open,
        gate_enabled,
        load_config,
        log_debug,
        project_dry_dir,
        read_stdin_json,
        transcript_context_tokens,
    )
except Exception:  # import failure must never strand the session
    sys.exit(0)

WINDOW_1M = 1_000_000
WINDOW_200K = 200_000

BLOCK_STDERR = "dry gate: deferring auto-compact until a boundary (release: touch {flag})"

# A pending marker this old is debris from an earlier session, not a live episode.
STALE_PENDING_MINUTES = 24 * 60


def real_window(info: dict) -> int:
    model = (info.get("model") or "").lower()
    small = (
        not model
        or "haiku" in model
        or os.environ.get("CLAUDE_CODE_DISABLE_1M_CONTEXT") == "1"
    )
    return WINDOW_200K if small else WINDOW_1M


def marker_paths(dry_dir: Path, cwd: str, name: str) -> list[Path]:
    """Project-root location first, then the shell-cwd location if different."""
    paths = [dry_dir / name]
    alt = Path(cwd) / ".claude" / "dry" / name
    if alt.parent != dry_dir:
        paths.append(alt)
    return paths


def consume_flag(dry_dir: Path, cwd: str, cfg: dict) -> bool | None:
    """Remove every compact-ok flag; True if any was fresh, False if only
    stale ones existed, None if there was no flag at all."""
    result = None
    for flag in marker_paths(dry_dir, cwd, "compact-ok"):
        try:
            age = time.time() - flag.stat().st_mtime
        except OSError:
            continue
        flag.unlink(missing_ok=True)  # one-shot, consumed even when stale
        if age <= cfg["gate_flag_max_age_minutes"] * 60:
            result = True
        elif result is None:
            result = False
    return result


def clear_pending(dry_dir: Path, cwd: str) -> None:
    for marker in marker_paths(dry_dir, cwd, "compact-pending"):
        marker.unlink(missing_ok=True)


def allow(dry_dir: Path, cwd: str, kind: str, detail: str = "") -> None:
    """Let the compaction through and record why, for dry_rehydrate (which
    reads the marker at SessionStart) and dry_checkpoint (which consumes it
    at PostCompact and writes the ledger line)."""
    clear_pending(dry_dir, cwd)
    atomic_write(dry_dir / "compact-released", f"{kind} {detail}".strip() + "\n")
    log_debug(f"gate: allowed ({kind}{' ' + detail if detail else ''})")


def main() -> None:
    data = read_stdin_json()
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return
    cfg = load_config(cwd)
    if cfg["disable"]:
        return
    if not gate_enabled(cfg):
        return

    dry_dir = project_dry_dir(cwd, create=True)
    pending = dry_dir / "compact-pending"

    if data.get("trigger") != "auto":
        clear_pending(dry_dir, cwd)  # the user compacted; any pending episode is over
        return  # manual /compact is the user's call; always allow

    fresh = consume_flag(dry_dir, cwd, cfg)
    if fresh:
        allow(dry_dir, cwd, "flag")
        return
    if fresh is False:
        log_debug("gate: stale compact-ok flag ignored")

    info = transcript_context_tokens(str(data.get("transcript_path") or ""))
    if info is None:
        allow(dry_dir, cwd, "blind")  # can't see context size -> never block blind
        return
    if info["tokens"] >= cfg["gate_failsafe_fraction"] * real_window(info):
        allow(dry_dir, cwd, "failsafe")
        return

    try:
        pending_minutes = (time.time() - pending.stat().st_mtime) / 60
    except OSError:
        pending_minutes = None
    if pending_minutes is not None and pending_minutes >= STALE_PENDING_MINUTES:
        pending.unlink(missing_ok=True)  # debris: start a fresh episode below
        pending_minutes = None
    max_pending = cfg["gate_max_pending_minutes"]
    if (
        pending_minutes is not None
        and isinstance(max_pending, (int, float))
        and max_pending > 0
        and pending_minutes >= max_pending
    ):
        allow(dry_dir, cwd, "timeout", str(int(round(pending_minutes))))
        return

    # Block. Create the pending marker only if absent — its mtime is the
    # dedupe key dry_watch uses, so re-blocks must not refresh it.
    if not pending.exists():
        atomic_write(pending, "a dry_gate-deferred auto-compact is pending\n")
    print(BLOCK_STDERR.format(flag=dry_dir / "compact-ok"), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    fail_open(main)
