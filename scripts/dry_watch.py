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

# Interactive runs (no gate): the user runs /compact or /clear; the agent
# can only suggest. Kept to one calm line at a boundary.
ADVISORIES = (
    "[dry] Context check: ~{tokens:,} tokens (~{pct}% of your {ref:,}-token"
    " working budget). Largest tool results so far: {hogs}. Attention quality"
    " degrades well before hard limits. Consider now: (1) update the task"
    " ledger — dry:context-ledger skill, .claude/dry/ledger.md; (2) route heavy"
    " reads/searches through subagents; (3) redirect long command output to"
    " files (cmd > /tmp/out.txt && tail -20 /tmp/out.txt).",
    "[dry] Context check: ~{tokens:,} tokens (~{pct}% of {ref:,}). You are in"
    " the degradation zone. Finish the current subtask, update the ledger"
    " (.claude/dry/ledger.md: decisions, files, gotchas, next), and avoid"
    " opening new exploration before checkpointing. When you next report to"
    " the user, note in one line that this is a good boundary for /compact"
    " (with ledger-derived keep-instructions) or /clear — dry rehydrates from"
    " the ledger either way.",
    "[dry] Context check: ~{tokens:,} tokens (~{pct}% of {ref:,}) — near or"
    " past the end of your working budget. Checkpoint now: update .claude/dry/ledger.md"
    " first and keep further tool output minimal. In your next user-facing"
    " message, suggest /compact with specific keep-instructions from the"
    " ledger, in one line. A full pre-compact transcript snapshot is saved"
    " automatically.",
)

# Gated runs (DRY_GATE=1): the agent owns compaction and releases it itself
# at a boundary, but only once the harness has actually attempted one (the
# pending notice below). Never ask the user; never narrate context state.
GATE_ADVISORIES = (
    "[dry] Context check: ~{tokens:,} tokens (~{pct}% of your {ref:,}-token"
    " working budget). Largest tool results so far: {hogs}. Attention quality"
    " degrades well before hard limits. Consider now: (1) update the task"
    " ledger — dry:context-ledger skill, .claude/dry/ledger.md; (2) route heavy"
    " reads/searches through subagents; (3) redirect long command output to"
    " files (cmd > /tmp/out.txt && tail -20 /tmp/out.txt). Gated run: you"
    " will compact yourself later — nothing to raise with the user.",
    "[dry] Context check: ~{tokens:,} tokens (~{pct}% of {ref:,}). Gated run:"
    " compaction is yours to take, not the user's — do not propose /compact"
    " or /clear and do not report context state. Do now: finish the current"
    " subtask before opening new exploration, and bring .claude/dry/ledger.md"
    " current (decisions with whys, files, gotchas, next). Compaction becomes"
    " available only when a [dry] pending notice arrives; until then, keep"
    " working.",
    "[dry] Context check: ~{tokens:,} tokens (~{pct}% of {ref:,}). You are near"
    " or past your working budget — the minimum at which a compaction pays off,"
    " not a deadline. Keep .claude/dry/ledger.md current and tool output lean, and"
    " carry on. When a [dry] pending notice arrives, release the gate at your"
    " next clean boundary (touch .claude/dry/compact-ok). Nothing to tell the"
    " user.",
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


def build_advisory(band: int, tokens: int, ref: int, top: list, gated: bool = False) -> str:
    templates = GATE_ADVISORIES if gated else ADVISORIES
    template = templates[min(band, len(templates) - 1)]
    text = template.format(
        tokens=tokens,
        pct=round(100 * tokens / ref),
        ref=ref,
        hogs=format_hogs(top),
    )
    return _common.cap_text(text, ADVISORY_MAX)


PENDING_NOTICE = (
    "[dry] A gated auto-compact is pending: the harness asked to compact and"
    " dry_gate deferred it for you, so compaction is now available. It waits"
    " until you release it — no rush, and nothing to ask or tell the user."
    " At your next clean boundary (not mid-subtask): bring the ledger"
    " ({ledger}) current, then run: touch {flag} — compaction proceeds on the"
    " next attempt and dry rehydrates you from the ledger. Until then, keep"
    " working. (Failsafe: compaction is forced only near the model's real"
    " window limit.)"
)

PENDING_REMINDER = (
    "[dry] Reminder: a gated auto-compact has been pending for ~{mins} min."
    " It keeps waiting for you. Whenever you reach a clean boundary, bring"
    " {ledger} current and run: touch {flag}. Nothing to tell the user."
)


def pending_notice(cwd: str, watch: dict, cfg: dict) -> str | None:
    """One notice per gate episode (keyed on the pending marker's mtime),
    then a reminder every gate_remind_minutes while it stays pending."""
    dry_dir = _common.project_dry_dir(cwd)
    marker = dry_dir / "compact-pending"
    try:
        mtime = int(marker.stat().st_mtime)
    except OSError:
        watch.pop("pending_seen", None)
        watch.pop("pending_reminded", None)
        return None
    now = _common.wall()
    paths = {"ledger": str(dry_dir / "ledger.md"), "flag": str(dry_dir / "compact-ok")}
    if watch.get("pending_seen") != mtime:
        watch["pending_seen"] = mtime
        watch["pending_reminded"] = now
        return _common.cap_text(PENDING_NOTICE.format(**paths), ADVISORY_MAX)
    remind = cfg.get("gate_remind_minutes")
    last = watch.get("pending_reminded")
    if not isinstance(last, (int, float)):
        watch["pending_reminded"] = now
        return None
    if isinstance(remind, (int, float)) and remind > 0 and now - last >= remind * 60:
        watch["pending_reminded"] = now
        mins = max(1, round((now - mtime) / 60))
        return _common.cap_text(PENDING_REMINDER.format(mins=mins, **paths), ADVISORY_MAX)
    return None


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
        gated = _common.gate_enabled(cfg)
        return build_advisory(band, tokens, int(ref), watch["top"], gated)
    return None


def main() -> None:
    data = _common.read_stdin_json()
    cfg = _common.load_config(data["cwd"])
    if cfg["disable"] or cfg["disable_watch"]:
        return
    event = data.get("hook_event_name")
    if event not in ("UserPromptSubmit", "PostToolUse"):
        return
    if data.get("agent_id") or data.get("agent_type"):
        # Subagent tool call: the payload's transcript is the PARENT's, so any
        # advisory would describe the parent's context and the pending notice
        # would invite the subagent to release the parent's gate. Stay silent.
        return
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return
    state = _common.load_state(session_id)
    watch = watch_state(state)
    # The gate's pending marker outranks throttling: the agent must learn
    # promptly that a deferred compaction is waiting on it.
    notice = pending_notice(data["cwd"], watch, cfg)

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
