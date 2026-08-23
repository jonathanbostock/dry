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
      DRY_GATE=1 claude --autocompact 300000
  - trigger "manual": always allowed — the user asked for it.
  - trigger "auto" + fresh <cwd>/.claude/dry/compact-ok flag: allow and
    consume the flag (one-shot). The agent creates it at a boundary:
      touch .claude/dry/compact-ok
  - trigger "auto", no flag: block (exit 2) and leave a compact-pending
    marker, which dry_watch turns into an in-context notice so the agent
    knows to wrap up and release.
  - Failsafe: near the model's real window the gate always allows — blocking
    an error-recovery compaction would fail the request outright.

Fail-open direction is ALLOW: any internal error -> exit 0 (never strand a
session behind a broken gate).
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _common import (
        atomic_write,
        fail_open,
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

BLOCK_STDERR = (
    "dry gate: deferring auto-compact until a boundary "
    "(release: touch .claude/dry/compact-ok)"
)


def env_enabled() -> bool:
    return os.environ.get("DRY_GATE", "").strip().lower() in ("1", "true", "yes", "on")


def failsafe_release(data: dict, cfg: dict) -> bool:
    """True when we must allow: blind, or near the model's real window."""
    info = transcript_context_tokens(str(data.get("transcript_path") or ""))
    if info is None:
        return True  # can't see context size -> never block blind
    model = (info.get("model") or "").lower()
    small = (
        not model
        or "haiku" in model
        or os.environ.get("CLAUDE_CODE_DISABLE_1M_CONTEXT") == "1"
    )
    window = WINDOW_200K if small else WINDOW_1M
    return info["tokens"] >= cfg["gate_failsafe_fraction"] * window


def main() -> None:
    data = read_stdin_json()
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return
    cfg = load_config(cwd)
    if cfg["disable"]:
        return
    if not (cfg["gate_enabled"] or env_enabled()):
        return
    if data.get("trigger") != "auto":
        return  # manual /compact is the user's call; always allow

    dry_dir = project_dry_dir(cwd, create=True)
    flag = dry_dir / "compact-ok"
    pending = dry_dir / "compact-pending"

    try:
        flag_age = time.time() - flag.stat().st_mtime
    except OSError:
        flag_age = None
    if flag_age is not None:
        flag.unlink(missing_ok=True)  # one-shot, consumed even when stale
        if flag_age <= cfg["gate_flag_max_age_minutes"] * 60:
            pending.unlink(missing_ok=True)
            log_debug("gate: released by compact-ok flag")
            return  # allow
        log_debug("gate: stale compact-ok flag ignored")

    if failsafe_release(data, cfg):
        pending.unlink(missing_ok=True)
        log_debug("gate: failsafe release (blind or near window limit)")
        return  # allow

    # Block. Create the pending marker only if absent — its mtime is the
    # dedupe key dry_watch uses, so re-blocks must not refresh it.
    if not pending.exists():
        atomic_write(pending, "a dry_gate-deferred auto-compact is pending\n")
    print(BLOCK_STDERR, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    fail_open(main)
