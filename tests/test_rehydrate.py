"""Subprocess tests for scripts/dry_rehydrate.py (SessionStart compact|clear|resume).

The hook always exits 0; when it has something to say it emits a single JSON
object with hookSpecificOutput.additionalContext, otherwise stdout is empty.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dry_rehydrate.py"
SESSION_ID = "a8ba0f4e-438f-4a93-853e-61bc793d52f5"

LEDGER = """# dry ledger

## Goal
Ship the dry plugin

## Now
- wiring rehydrate

## Done
- checkpoint built

## Next
- smoke test
"""

def run_hook(tmp_path, payload=None, raw=None, extra_env=None):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": str(tmp_path),  # keep debug.log/state out of the real /tmp
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if extra_env:
        env.update(extra_env)
    data = raw if raw is not None else json.dumps(payload).encode()
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=data,
        capture_output=True,
        env=env,
        timeout=60,
    )


def make_project(tmp_path: Path) -> Path:
    proj = tmp_path / "project"
    proj.mkdir(exist_ok=True)
    return proj


def write_ledger(proj: Path, text: str = LEDGER) -> Path:
    d = proj / ".claude" / "dry"
    d.mkdir(parents=True, exist_ok=True)
    ledger = d / "ledger.md"
    ledger.write_text(text, encoding="utf-8")
    return ledger


def payload(proj: Path, source: str) -> dict:
    return {
        "session_id": SESSION_ID,
        "transcript_path": "/nonexistent/transcript.jsonl",
        "cwd": str(proj),
        "hook_event_name": "SessionStart",
        "source": source,
    }


def context_of(proc) -> str:
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    return hso["additionalContext"]


# --- source == "compact" --------------------------------------------------


def test_compact_with_ledger_and_snapshot(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj)
    sd = proj / ".claude" / "dry" / "snapshots"
    sd.mkdir(parents=True)
    (sd / "20250101T000000Z-a8ba0f4e-manual.jsonl.gz").write_bytes(b"old")
    (sd / "20250601T000000Z-a8ba0f4e-auto.jsonl.gz").write_bytes(b"new")
    text = context_of(run_hook(tmp_path, payload(proj, "compact")))
    assert "[dry] Context was just compacted" in text
    assert "authoritative" in text
    assert "Ship the dry plugin" in text  # ledger body present
    assert "- wiring rehydrate" in text
    assert ".claude/dry/snapshots/20250601T000000Z-a8ba0f4e-auto.jsonl.gz" in text
    assert "20250101T000000Z" not in text  # only the newest snapshot is cited


def test_compact_with_ledger_no_snapshot(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj)
    text = context_of(run_hook(tmp_path, payload(proj, "compact")))
    assert "Ship the dry plugin" in text
    assert "pre-compact transcript snapshot" not in text


DIGEST_MARKER_HEAD = "[dry: newest-first digest;"


def _inner(text: str) -> str:
    return text.split("---\n", 1)[1].rsplit("\n---", 1)[0]


def test_short_ledger_injected_unchanged(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj)
    inner = _inner(context_of(run_hook(tmp_path, payload(proj, "compact"))))
    assert inner == LEDGER
    assert "digest" not in inner


def test_compact_long_ledger_is_newest_first_digest(tmp_path):
    proj = make_project(tmp_path)
    body = "\n".join(
        f"- 2026-01-01T{i // 60:02d}:{i % 60:02d}Z decision {i}: keep helper {i} because reasons"
        for i in range(400)
    )
    write_ledger(proj, "# dry ledger\n\n## Goal\nBig task\n\n## Log\n" + body + "\n- FINAL-UNIQUE-TAIL newest entry\n")
    text = context_of(run_hook(tmp_path, payload(proj, "compact")))
    assert "newest-first digest" in text.split("---\n", 1)[0]  # the preamble says what this is
    inner = _inner(text)
    assert len(inner) <= 6000
    assert DIGEST_MARKER_HEAD in inner and "older ledger items omitted" in inner
    assert "Big task" in inner  # pinned Goal kept
    assert "FINAL-UNIQUE-TAIL" in inner  # newest item kept
    assert inner.index("FINAL-UNIQUE-TAIL") < inner.index("decision 399") < inner.index("decision 398")
    assert "decision 0:" not in inner  # oldest dropped
    assert inner.index("## Goal") < inner.index("## Log")


def test_digest_item_caps_and_stubs(tmp_path):
    proj = make_project(tmp_path)
    items = [f"- item{i:03d} " + "x" * 900 for i in range(60)]
    items.append("- newest " + "y" * 3000)
    write_ledger(proj, "## Log\n" + "\n".join(items) + "\n")
    inner = _inner(context_of(run_hook(tmp_path, payload(proj, "compact"))))
    lines = [l for l in inner.splitlines() if l.startswith("- ")]
    assert lines[0].startswith("- newest") and len(lines[0]) == 2000  # 1999 chars + ellipsis
    assert lines[1].startswith("- item059") and lines[2].startswith("- item058")  # newest first
    detail = [l for l in lines[1:] if len(l) == 500]
    assert len(detail) == 6  # 2000 + 6 x 500 = the 5,000-char detail budget
    stubs = lines[1 + len(detail):]
    assert stubs and all(len(l) == 110 for l in stubs)
    assert len(inner) <= 6000


def test_digest_pins_goal_and_now_and_groups_by_section(tmp_path):
    proj = make_project(tmp_path)
    pad = "\n".join(f"- 2026-02-02T00:00Z older note {i} " + "z" * 200 for i in range(80))
    write_ledger(
        proj,
        "# L\n\n## Goal\nShip X\n\n## Now\ncurrent step Q\n\n## Ancient\n" + pad
        + "\n\n## Recent\n- 2026-03-03T00:00Z latest thing\n",
    )
    inner = _inner(context_of(run_hook(tmp_path, payload(proj, "compact"))))
    assert inner.startswith("# L\n## Goal\nShip X\n## Now\ncurrent step Q\n## Recent\n- 2026-03-03T00:00Z latest thing\n## Ancient\n")
    assert inner.index("older note 79") < inner.index("older note 78")
    assert "older note 0 " not in inner
    assert "Older sections" not in inner  # every section header was reached


def test_digest_lists_sections_it_never_reached(tmp_path):
    proj = make_project(tmp_path)
    old = "\n".join(f"- old {i} " + "q" * 300 for i in range(40))
    new = "\n".join(f"- new {i} " + "r" * 900 for i in range(60))  # the budget runs out inside this section
    write_ledger(proj, "## First task\n" + old + "\n\n## Second task\n" + new + "\n")
    inner = _inner(context_of(run_hook(tmp_path, payload(proj, "compact"))))
    assert "## Second task" in inner and "## First task" not in inner
    assert "Older sections, items omitted: First task" in inner


def test_digest_cap_configurable(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj, "## Log\n" + "\n".join(f"- entry {i} " + "w" * 100 for i in range(100)) + "\n")
    (proj / ".claude" / "dry" / "config.json").write_text('{"rehydrate_ledger_chars": 1500}')
    inner = _inner(context_of(run_hook(tmp_path, payload(proj, "compact"))))
    assert len(inner) <= 1500 and "entry 99 " in inner and "entry 0 " not in inner
    env = {"DRY_REHYDRATE_LEDGER_CHARS": "900"}
    inner = _inner(context_of(run_hook(tmp_path, payload(proj, "compact"), extra_env=env)))
    assert len(inner) <= 900 and "entry 99 " in inner


def test_parse_ledger_items_fences_and_continuations():
    sys.path.insert(0, str(SCRIPT.parent))
    import dry_rehydrate as r

    parts = r.parse_ledger("# T\n\n## S\n- one\n  cont\n- two\n```\n- not an item\n```\npara line\nmore\n\n1. three\n")
    assert parts[0] == ("title", "# T") and parts[1] == ("section", "## S")
    items = [t for k, t in parts if k == "item"]
    assert items == ["- one\n  cont", "- two\n```\n- not an item\n```\npara line\nmore", "1. three"]


def test_compact_without_ledger_prompts_creation(tmp_path):
    proj = make_project(tmp_path)
    text = context_of(run_hook(tmp_path, payload(proj, "compact")))
    assert "no task ledger exists" in text
    assert ".claude/dry/ledger.md" in text
    assert "context-ledger" in text


# --- source in ("clear", "resume") -----------------------------------------


def test_pointer_on_clear_and_resume(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj)
    for source in ("clear", "resume"):
        text = context_of(run_hook(tmp_path, payload(proj, source)))
        assert "\n" not in text  # one line only
        assert "(goal: Ship the dry plugin)" in text
        assert ".claude/dry/ledger.md" in text
        assert "ago" in text
        assert "- wiring rehydrate" not in text  # pointer, not the body


def test_pointer_age_humanized(tmp_path):
    proj = make_project(tmp_path)
    ledger = write_ledger(proj)
    t = time.time() - 3 * 3600 - 120
    os.utime(ledger, (t, t))
    assert "3h ago" in context_of(run_hook(tmp_path, payload(proj, "clear")))
    t = time.time() - 2 * 86400 - 3600
    os.utime(ledger, (t, t))
    assert "2d ago" in context_of(run_hook(tmp_path, payload(proj, "resume")))


def test_stale_ledger_silent(tmp_path):
    proj = make_project(tmp_path)
    ledger = write_ledger(proj)
    t = time.time() - 8 * 86400  # default max age is 7 days
    os.utime(ledger, (t, t))
    for source in ("clear", "resume"):
        proc = run_hook(tmp_path, payload(proj, source))
        assert proc.returncode == 0 and proc.stdout == b""


def test_clear_without_ledger_silent(tmp_path):
    proj = make_project(tmp_path)
    proc = run_hook(tmp_path, payload(proj, "clear"))
    assert proc.returncode == 0 and proc.stdout == b""
    assert not (proj / ".claude").exists()  # rehydrate never creates dirs


def test_goal_fallback_first_content_line(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj, "# dry ledger\n\n- fixed the flux capacitor\n- other\n")
    text = context_of(run_hook(tmp_path, payload(proj, "clear")))
    assert "(goal: - fixed the flux capacitor)" in text


def test_goal_untitled_when_only_headings(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj, "# dry ledger\n\n## Goal\n\n## Now\n")
    text = context_of(run_hook(tmp_path, payload(proj, "clear")))
    assert "(goal: untitled)" in text


def test_goal_capped_at_150(tmp_path):
    proj = make_project(tmp_path)
    goal = "G" * 300
    write_ledger(proj, f"## Goal\n{goal}\n")
    text = context_of(run_hook(tmp_path, payload(proj, "clear")))
    assert goal not in text
    assert ("G" * 149 + "…") in text


# --- fail-open behaviour ----------------------------------------------------


def test_disable_env_vars_silent(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj)
    for var in ("DRY_DISABLE", "DRY_DISABLE_REHYDRATE"):
        proc = run_hook(tmp_path, payload(proj, "compact"), extra_env={var: "true"})
        assert proc.returncode == 0 and proc.stdout == b""


def test_unknown_source_silent(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj)
    proc = run_hook(tmp_path, payload(proj, "startup"))
    assert proc.returncode == 0 and proc.stdout == b""


def test_malformed_stdin_exits_zero(tmp_path):
    for raw in (b"", b"{", b'"str"', b'{"source": "compact"}'):
        proc = run_hook(tmp_path, raw=raw)
        assert proc.returncode == 0 and proc.stdout == b""


# ------------------------------------------ reset kind, pending clear, pruning

def _dry(proj: Path) -> Path:
    d = proj / ".claude" / "dry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_compact_announces_release_kind_without_consuming_marker(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj)
    dry = _dry(proj)
    (dry / "compact-released").write_text("flag\n")
    (dry / "compact-pending").write_text("pending\n")
    text = context_of(run_hook(tmp_path, payload(proj, "compact")))
    assert "your own boundary release" in text
    assert (dry / "compact-released").exists()  # PostCompact consumes it, not us
    assert not (dry / "compact-pending").exists()  # the pending episode is over


def test_compact_announces_failsafe_and_timeout(tmp_path):
    proj = make_project(tmp_path)
    write_ledger(proj)
    dry = _dry(proj)
    (dry / "compact-released").write_text("failsafe\n")
    assert "forced by the gate's failsafe" in context_of(run_hook(tmp_path, payload(proj, "compact")))
    (dry / "compact-released").write_text("timeout 95\n")
    assert "pending ~95 min without release" in context_of(run_hook(tmp_path, payload(proj, "compact")))


def test_compact_prunes_old_bookkeeping_lines(tmp_path):
    proj = make_project(tmp_path)
    noise = "".join(
        f"\n- ⚠ 2026-08-24T18:{i:02d}:00+00:00: auto-compact fired mid-task; pre-compact snapshot: s{i}.gz\n"
        for i in range(40)
    )
    write_ledger(
        proj,
        "# dry ledger\n" + noise + "\n## Goal\nShip it\n\n## Now\nstep 3\n\n"
        "- ✓ 2026-09-24T12:00:00+00:00: compaction released at a boundary (gated run)\n",
    )
    text = context_of(run_hook(tmp_path, payload(proj, "compact")))
    assert "auto-compact fired mid-task" not in text
    assert "compaction released at a boundary" in text
    assert "## Goal" in text and "## Now" in text
