#!/usr/bin/env python3
"""RESET leg, read side: re-anchor the agent after compact/clear/resume.

SessionStart hook, matcher compact|clear|resume (see hooks/hooks.json).

  source == "compact"       — inject the task ledger: unchanged when it fits
                              the cap (6k chars), otherwise a NEWEST-FIRST
                              digest (Goal/Now pinned, then the latest items
                              under their section headers: the newest up to
                              2,000 chars, the next up to 500 each until
                              5,000 chars of items, then one-line stubs,
                              closed by a marker counting the older items
                              left out; caps are rehydrate_* config keys),
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
import re
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
NEWEST_ITEM_CAP = 2_000
ITEM_CAP = 500
DETAIL_BUDGET = 5_000
STUB_CAP = 110
PINNED_SECTIONS = ("goal", "now")  # template sections shown first, in full (capped per item)
DIGEST_MARKER = ("\n[dry: newest-first digest; {omitted} older ledger items omitted and shown"
                 " items trimmed — Read .claude/dry/ledger.md for the rest]")
_ITEM_START = re.compile(r"^([-*+] |\d+[.)] )")

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


# ------------------------------------------------------------ newest-first digest

def parse_ledger(text: str) -> list[tuple[str, str]]:
    """Split a ledger into ('title' | 'section' | 'item', text), in document order.

    The first '# ' line is the title and every other heading a section. An item
    is a bullet ('- ', '* ', '+ ', '1. ') or a paragraph; indented lines, plain
    lines that follow a bullet and the inside of a ``` fence attach to the
    current item, and a blank line ends it.
    """
    parts: list[tuple[str, str]] = []
    cur: list[str] = []
    in_fence = False
    saw_title = False

    def flush() -> None:
        nonlocal cur
        if cur:
            parts.append(("item", "\n".join(cur)))
            cur = []

    for line in text.splitlines():
        stripped = line.strip()
        if in_fence:
            cur.append(line)
            in_fence = not stripped.startswith("```")
            continue
        if stripped.startswith("```"):
            in_fence = True
            cur.append(line)
            continue
        if not stripped:
            flush()
            continue
        if stripped.startswith("#"):
            flush()
            if line.startswith("# ") and not saw_title:
                saw_title = True
                parts.append(("title", stripped))
            else:
                parts.append(("section", stripped))
            continue
        if cur and (line[:1].isspace() or not _ITEM_START.match(stripped)):
            cur.append(line)
            continue
        flush()
        cur = [line]
    flush()
    return parts


def _trim(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: max(1, cap - 1)] + "…"


def _stub(text: str, cap: int) -> str:
    return _trim(" ".join(text.split()), cap)


def digest_caps(cfg: dict | None) -> dict:
    caps = {
        "ledger": LEDGER_CAP,
        "newest": NEWEST_ITEM_CAP,
        "item": ITEM_CAP,
        "detail": DETAIL_BUDGET,
        "stub": STUB_CAP,
    }
    keys = {
        "ledger": "rehydrate_ledger_chars",
        "newest": "rehydrate_newest_item_chars",
        "item": "rehydrate_item_chars",
        "detail": "rehydrate_detail_chars",
        "stub": "rehydrate_stub_chars",
    }
    for name, key in keys.items():
        value = (cfg or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 40:
            caps[name] = int(value)
    return caps


def ledger_digest(text: str, caps: dict) -> str:
    """Newest-first view of a ledger too long to inject whole.

    Title, then the pinned sections (Goal, Now) in document order, then the
    sections walked last-to-first with their items newest-first under their own
    header. The newest item is trimmed to caps['newest'] chars, the following
    ones to caps['item'] until caps['detail'] chars of items are shown, and the
    rest become one-line stubs of caps['stub'] chars, until the whole text
    reaches caps['ledger']. Section headers whose items were all left out are
    listed at the end, before a marker that counts the omitted items.
    """
    parts = parse_ledger(text)
    title = next((t for kind, t in parts if kind == "title"), None)
    groups: list[tuple[str | None, list[str]]] = []
    for kind, t in parts:
        if kind == "title":
            continue
        if kind == "section":
            groups.append((t, []))
        else:
            if not groups:
                groups.append((None, []))
            groups[-1][1].append(t)
    total_items = sum(len(items) for _, items in groups)

    def pinned(header: str | None) -> bool:
        return header is not None and header.lstrip("#").strip().lower() in PINNED_SECTIONS

    out: list[str] = []
    used = 0
    # Room for the closing marker, and for the list of section headers the walk
    # never reaches (the task statements), which is worth more than two stubs.
    n_walked = sum(1 for h, _ in groups if h is not None and not pinned(h))
    sections_reserve = 200 if n_walked > 1 else 0
    limit = caps["ledger"] - len(DIGEST_MARKER.format(omitted=total_items)) - sections_reserve

    def room(piece: str) -> bool:
        return used + len(piece) + 1 <= limit

    def emit(piece: str) -> bool:
        nonlocal used
        if not room(piece):
            return False
        out.append(piece)
        used += len(piece) + 1
        return True

    shown = 0
    if title:
        emit(title)
    for header, items in groups:
        if not pinned(header):
            continue
        emit(header)
        for item in items:
            if emit(_trim(item, caps["item"])):
                shown += 1

    detail_used = 0
    n_detail = 0
    full = True
    unshown_headers: list[str] = []
    for header, items in reversed(groups):
        if pinned(header):
            continue
        header_done = header is None
        for item in reversed(items):
            if full:
                cap = caps["newest"] if n_detail == 0 else caps["item"]
                piece = _trim(item, cap)
                if detail_used + len(piece) > caps["detail"] and n_detail > 0:
                    full = False
            if not full:
                piece = _stub(item, caps["stub"])
            header_piece = "" if header_done else header
            if header_piece and not room(header_piece + "\n" + piece):
                full = False  # stop everything below; the header goes to the omitted list
                break
            if not room(piece):
                break
            if header_piece:
                emit(header_piece)
                header_done = True
            emit(piece)
            shown += 1
            if full:
                detail_used += len(piece)
                n_detail += 1
        if not header_done and header is not None:
            unshown_headers.append(header)
        if used >= limit:
            break
    # Headers reached only after the budget ran out, oldest sections last.
    seen = set(unshown_headers)
    for header, _ in reversed(groups):
        if header is not None and not pinned(header) and header not in seen and header not in out:
            unshown_headers.append(header)
    limit += sections_reserve
    if unshown_headers:
        line = "Older sections, items omitted: " + "; ".join(h.lstrip("#").strip() for h in unshown_headers)
        emit(_trim(line, max(40, limit - used - 1)))
    omitted = max(0, total_items - shown)
    return "\n".join(out) + DIGEST_MARKER.format(omitted=omitted)


def ledger_view(text: str, caps: dict) -> str:
    """The ledger as injected: unchanged when it fits, else the newest-first digest."""
    text = prune_bookkeeping(text)
    if len(text) <= caps["ledger"]:
        return text
    return ledger_digest(text, caps)


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


def compact_context(cwd: str, ledger: Path, cfg: dict | None = None) -> str:
    dry_dir = project_dry_dir(cwd)
    if not ledger.is_file():
        return NO_LEDGER_MSG + released_note(dry_dir)
    text = ledger_view(read_head(ledger), digest_caps(cfg))
    digestnote = ""
    if DIGEST_MARKER[:28] in text:
        digestnote = ("; it is longer than the injection cap, so this is a newest-first"
                      " digest: latest items first, older ones omitted")
    snap = newest_snapshot(dry_dir / "snapshots")
    snapnote = ""
    if snap is not None:
        snapnote = f" (full pre-compact transcript snapshot: {rel_to(snap, cwd)})"
    return (
        "[dry] Context was just compacted. The native summary above may have "
        f"dropped specifics.{released_note(dry_dir)} The task ledger below is "
        f"authoritative{snapnote}{digestnote}. Re-read it before continuing.\n\n---\n{text}\n---"
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
        text = compact_context(data["cwd"], ledger, cfg)
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
