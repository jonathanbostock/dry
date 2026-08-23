"""dry_watch.py — SEE leg: context-usage advisories at band crossings.

Hook for UserPromptSubmit (always a full check) and PostToolUse matcher "*"
(throttled; also tracks the largest tool results seen so far). Advisories are
edge-triggered: injected only when usage crosses a configured band upward;
a downward move (compaction / clear happened) resets the band silently.

Fail-open per the plugin contract: any anomaly -> exit 0, no output.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _common
except Exception:  # import failure must never break the session
    sys.exit(0)

TRACK_MIN_CHARS = 2_000  # tool results smaller than this are never hogs
TOP_KEEP = 5             # hog entries kept in state
HOGS_SHOWN = 3           # hog entries shown in an advisory
TOOL_NAME_MAX = 40       # keep advisory length bounded for long MCP names
ADVISORY_MAX = 999       # hard cap: every advisory stays under 1000 chars

ADVISORIES = (
    "[dry] Context check: ~{tokens:,} tokens (~{pct}% of your {ref:,}-token"
    " working budget). Largest tool results so far: {hogs}. Attention quality"
    " degrades well before hard limits. Consider now: (1) update the task"
    " ledger — dry:context-ledger skill, .claude/dry/ledger.md; (2) route heavy"
    " reads/searches through subagents; (3) redirect long command output to"
    " files (cmd > /tmp/out.txt && tail -20 /tmp/out.txt).",
    "[dry] Context check: ~{tokens:,} tokens (~{pct}% of {ref:,}). You are in"
    " the degradation zone. Finish the current subtask, update the ledger"
    " (.claude/dry/ledger.md: decisions, files, gotchas, next), then treat"
    " this as a boundary: recommend the user run /compact with ledger-derived"
    " instructions, or /clear (dry rehydrates automatically). Avoid opening"
    " new exploration before checkpointing.",
    "[dry] Context check: ~{tokens:,} tokens (~{pct}% of {ref:,}) — near the"
    " compaction point. Checkpoint NOW: update .claude/dry/ledger.md first,"
    " keep further tool output minimal, and in your next user-facing message"
    " propose compaction with specific keep-instructions from the ledger."
    " A full pre-compact transcript snapshot is saved automatically.",
)


def watch_state(state: dict) -> dict:
    """Return state["watch"], creating and sanitizing it in place."""
    w = state.get("watch")
    if not isinstance(w, dict):
        w = {}
    state["watch"] = w
    if not isinstance(w.get("last_check_wall"), (int, float)):
        w["last_check_wall"] = _common.wall()
    if not isinstance(w.get("calls_since"), int):
        w["calls_since"] = 0
    if not isinstance(w.get("last_band"), int):
        w["last_band"] = -1
    if not isinstance(w.get("top"), list):
        w["top"] = []
    return w


def track_hog(watch: dict, tool_name: str, size: int) -> None:
    """Merge one (size, tool) observation into the top-K hog list."""
    top = [
        e
        for e in watch.get("top", [])
        if isinstance(e, list) and len(e) == 2
        and isinstance(e[0], (int, float)) and isinstance(e[1], str)
    ]
    top.append([int(size), tool_name[:TOOL_NAME_MAX]])
    top.sort(key=lambda e: -e[0])
    watch["top"] = top[:TOP_KEEP]


def format_hogs(top: list | None) -> str:
    """Render hogs like 'Read 72k, Bash 36k chars', or a fixed placeholder."""
    entries = [
        e for e in (top or [])
        if isinstance(e, (list, tuple)) and len(e) == 2
    ]
    if not entries:
        return "none tracked yet"
    parts = [f"{name} {round(size / 1000)}k" for size, name in entries[:HOGS_SHOWN]]
    return ", ".join(parts) + " chars"


def compute_band(pct: float, bands: list) -> int:
    """Highest index b with pct >= bands[b], else -1 (non-numeric skipped)."""
    band = -1
    for i, threshold in enumerate(bands):
        if isinstance(threshold, (int, float)) and pct >= threshold:
            band = i
    return band


def build_advisory(band: int, tokens: int, ref: int, top: list) -> str:
    template = ADVISORIES[min(band, len(ADVISORIES) - 1)]
    text = template.format(
        tokens=tokens,
        pct=round(100 * tokens / ref),
        ref=ref,
        hogs=format_hogs(top),
    )
    return _common.cap_text(text, ADVISORY_MAX)


PENDING_NOTICE = (
    "[dry] A gated auto-compact is pending — dry_gate deferred it for you."
    " Finish the current step, bring .claude/dry/ledger.md current, then"
    " release it by running: touch .claude/dry/compact-ok — compaction"
    " proceeds at that boundary. (A failsafe releases automatically near the"
    " window limit.)"
)


def pending_notice(cwd: str, watch: dict) -> str | None:
    """One notice per gate episode: keyed on the pending marker's mtime."""
    marker = _common.project_dry_dir(cwd) / "compact-pending"
    try:
        mtime = int(marker.stat().st_mtime)
    except OSError:
        watch.pop("pending_seen", None)
        return None
    if watch.get("pending_seen") == mtime:
        return None
    watch["pending_seen"] = mtime
    return PENDING_NOTICE


def full_check(data: dict, cfg: dict, watch: dict) -> str | None:
    """Measure context, update band state; return advisory text or None."""
    watch["calls_since"] = 0
    watch["last_check_wall"] = _common.wall()
    info = _common.transcript_context_tokens(data["transcript_path"])
    if info is None:
        return None
    ref = cfg["reference_window"]
    if not isinstance(ref, (int, float)) or ref <= 0:
        return None
    tokens = info["tokens"]
    band = compute_band(tokens / ref, cfg["bands"])
    last_band = watch["last_band"]
    watch["last_band"] = band
    if band > last_band:
        return build_advisory(band, tokens, int(ref), watch["top"])
    return None


def main() -> None:
    data = _common.read_stdin_json()
    cfg = _common.load_config(data["cwd"])
    if cfg["disable"] or cfg["disable_watch"]:
        return
    event = data.get("hook_event_name")
    if event not in ("UserPromptSubmit", "PostToolUse"):
        return
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return
    state = _common.load_state(session_id)
    watch = watch_state(state)
    # The gate's pending marker outranks throttling: the agent must learn
    # promptly that a deferred compaction is waiting on it.
    notice = pending_notice(data["cwd"], watch)

    text = None
    if event == "PostToolUse":
        watch["calls_since"] += 1
        size = len(json.dumps(data.get("tool_response"), default=str))
        if size > TRACK_MIN_CHARS:
            track_hog(watch, str(data.get("tool_name", "?")), size)
        delta = _common.wall() - watch["last_check_wall"]
        throttled = (
            watch["calls_since"] < cfg["watch_min_calls"]
            and 0 <= delta < cfg["watch_min_seconds"]
        )  # negative delta = clock jump = treat as expired
        if not throttled:
            text = full_check(data, cfg, watch)
    else:
        text = full_check(data, cfg, watch)

    _common.save_state(session_id, state)
    combined = "\n\n".join(t for t in (notice, text) if t)
    if combined:
        _common.emit({
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": combined,
            }
        })


if __name__ == "__main__":
    _common.fail_open(main)
