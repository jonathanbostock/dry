"""dry_status.py — plain-text status report for the dry plugin (a CLI, not a hook).

Feeds the /dry:status command and curious humans: context tokens vs the
reference window, band, top hogs, ledger / snapshot / archive state, and one
suggested action. Missing pieces render as "unknown"; always exits 0.

Usage: dry_status.py [--cwd PATH] [--transcript PATH] [--session SID]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _common
    import dry_rehydrate
    import dry_watch
except Exception:  # import failure must never break the session or command
    sys.exit(0)

LEDGER_READ_MAX = 64 * 1024

BAND_NAMES = ("advisory", "degradation", "critical")

# Interactive runs: the user runs /compact or /clear; the agent may suggest.
ACTIONS = (
    "update the ledger (.claude/dry/ledger.md); route heavy reads/searches"
    " through subagents; redirect long command output to files.",
    "finish the current subtask, update the ledger, then note to the user in"
    " one line that this is a good /compact boundary (ledger-derived keep"
    " instructions) — or /clear; dry rehydrates either way.",
    "checkpoint NOW — update the ledger, keep tool output minimal, and suggest"
    " /compact with keep-instructions from the ledger in your next message.",
)

# Gated runs: the agent owns compaction; never route it through the user.
GATE_ACTIONS = (
    "update the ledger (.claude/dry/ledger.md); route heavy reads/searches"
    " through subagents; redirect long command output to files. Nothing to"
    " raise with the user.",
    "finish the current subtask and bring the ledger current, then keep"
    " working. Compaction becomes available when a [dry] pending notice"
    " arrives — do not propose /compact or /clear to the user.",
    "keep the ledger current and tool output lean; carry on. Release the gate"
    " at your next clean boundary once a compaction is pending.",
)

GATE_PENDING_ACTION = (
    "a gated auto-compact is pending: at your next clean boundary bring the"
    " ledger current, then release it — touch {flag}. Nothing to ask the user."
)


def munge(cwd: str) -> str:
    """Project-directory name Claude Code derives from a cwd."""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(Path(cwd).resolve()))


def config_home() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def find_transcript(cwd: str, explicit: str | None, sid: str | None) -> Path | None:
    """Discovery order: explicit path -> session id -> newest-mtime jsonl."""
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    project_dir = config_home() / "projects" / munge(cwd)
    if sid:
        candidate = project_dir / f"{sid}.jsonl"
        if candidate.is_file():
            return candidate
    try:
        return max(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    except (ValueError, OSError):
        return None


def band_name(band: int) -> str:
    if band < 0:
        return "ok"
    return BAND_NAMES[min(band, len(BAND_NAMES) - 1)]


def action_for(band: int | None, gated: bool = False, pending: bool = False,
               flag: str = ".claude/dry/compact-ok") -> str:
    if gated and pending:
        return GATE_PENDING_ACTION.format(flag=flag)
    if band is None:
        return "unknown"
    if band < 0:
        return "none — context healthy; keep the ledger current at task boundaries."
    actions = GATE_ACTIONS if gated else ACTIONS
    return actions[min(band, len(actions) - 1)]


def mode_line(gated: bool) -> str:
    if gated:
        return ("gated self-compaction (DRY_GATE) — you release compaction"
                " yourself at a boundary; never ask the user to compact")
    return "interactive — the user runs /compact or /clear; you may suggest a boundary"


def gate_line(dry_dir: Path, gated: bool) -> str:
    if not gated:
        return "off"
    marker = dry_dir / "compact-pending"
    if marker.is_file():
        age = humanize_age(_common.wall() - marker.stat().st_mtime)
        return (f"auto-compact pending for {age} — release at a clean boundary:"
                f" touch {dry_dir / 'compact-ok'}")
    return "on — nothing pending yet (compaction not available until the harness attempts one)"


def humanize_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def context_line(transcript: Path | None, cfg: dict) -> tuple[str, int | None]:
    """Return (report line, band or None)."""
    if transcript is None:
        return "unknown (no transcript found)", None
    info = _common.transcript_context_tokens(str(transcript))
    if info is None:
        return "unknown (no usage records in transcript)", None
    ref = cfg["reference_window"]
    if not isinstance(ref, (int, float)) or ref <= 0:
        return "unknown (bad reference_window)", None
    tokens = info["tokens"]
    pct = round(100 * tokens / ref)
    band = dry_watch.compute_band(tokens / ref, cfg["bands"])
    line = f"~{tokens:,} tokens (~{pct}% of {int(ref):,}) — band: {band_name(band)}"
    return line, band


def hogs_line(sid: str | None) -> str:
    if not sid:
        return "unknown"
    top = _common.load_state(sid).get("watch", {}).get("top")
    return dry_watch.format_hogs(top)


def ledger_line(ledger: Path) -> str:
    if not ledger.is_file():
        return "none (.claude/dry/ledger.md)"
    age = humanize_age(_common.wall() - ledger.stat().st_mtime)
    with open(ledger, encoding="utf-8", errors="replace") as f:
        text = f.read(LEDGER_READ_MAX)
    lines = text.splitlines()
    goal = _common.cap_text(dry_rehydrate.extract_goal(text), 150, "…")
    return f".claude/dry/ledger.md — {age} old, {len(lines)} lines — goal: {goal}"


def snapshots_line(snap_dir: Path) -> str:
    snaps = [p for p in snap_dir.iterdir() if p.is_file()] if snap_dir.is_dir() else []
    if not snaps:
        return "none"
    newest = max(snaps, key=lambda p: p.stat().st_mtime)
    return f"{len(snaps)} (newest: {newest.name})"


def archive_line(archive_dir: Path) -> str:
    files = [p for p in archive_dir.rglob("*") if p.is_file()] if archive_dir.is_dir() else []
    if not files:
        return "none"
    total_kb = sum(p.stat().st_size for p in files) / 1024
    return f"{len(files)} files, {total_kb:.1f} KB"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="dry plugin status report")
    parser.add_argument("--cwd", default=os.getcwd(), help="project directory")
    parser.add_argument("--transcript", default=None, help="explicit transcript path")
    parser.add_argument("--session", default=None, help="session id")
    return parser.parse_args()


def main() -> None:
    try:
        args = parse_args()
    except SystemExit:
        return  # bad args / --help: report nothing, exit 0 via fail_open
    cwd = str(Path(args.cwd).resolve())
    cfg = _common.load_config(cwd)
    dry_dir = _common.project_dry_dir(cwd)

    sid = args.session or os.environ.get("CLAUDE_SESSION_ID")
    transcript = find_transcript(cwd, args.transcript, sid)
    if not sid and transcript is not None:
        sid = transcript.stem

    try:
        ctx, band = context_line(transcript, cfg)
    except Exception:
        ctx, band = "unknown", None

    gated = _common.gate_enabled(cfg)
    pending = (dry_dir / "compact-pending").is_file()

    sections = (
        ("context:", lambda: ctx),
        ("mode:", lambda: mode_line(gated)),
        ("gate:", lambda: gate_line(dry_dir, gated)),
        ("top hogs:", lambda: hogs_line(sid)),
        ("ledger:", lambda: ledger_line(dry_dir / "ledger.md")),
        ("snapshots:", lambda: snapshots_line(dry_dir / "snapshots")),
        ("archive:", lambda: archive_line(dry_dir / "archive")),
        ("action:", lambda: action_for(band, gated, pending, str(dry_dir / "compact-ok"))),
    )
    out = [f"dry status — {cwd}"]
    for label, fn in sections:
        try:
            value = fn()
        except Exception:
            value = "unknown"
        out.append(f"{label:<11}{value}")
    print("\n".join(out))


if __name__ == "__main__":
    _common.fail_open(main)
