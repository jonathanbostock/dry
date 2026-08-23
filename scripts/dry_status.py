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

ACTIONS = (
    "update the ledger (.claude/dry/ledger.md); route heavy reads/searches"
    " through subagents; redirect long command output to files.",
    "finish the current subtask, update the ledger, then recommend /compact"
    " with ledger-derived instructions (or /clear) at this boundary.",
    "checkpoint NOW — update the ledger, keep tool output minimal, and"
    " propose /compact with keep-instructions from the ledger.",
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


def action_for(band: int | None) -> str:
    if band is None:
        return "unknown"
    if band < 0:
        return "none — context healthy; keep the ledger current at task boundaries."
    return ACTIONS[min(band, len(ACTIONS) - 1)]


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

    sections = (
        ("context:", lambda: ctx),
        ("top hogs:", lambda: hogs_line(sid)),
        ("ledger:", lambda: ledger_line(dry_dir / "ledger.md")),
        ("snapshots:", lambda: snapshots_line(dry_dir / "snapshots")),
        ("archive:", lambda: archive_line(dry_dir / "archive")),
        ("action:", lambda: action_for(band)),
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
