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

TRUNCATION_MARKER = "[dry: ledger truncated — Read .claude/dry/ledger.md for the rest]"


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


def test_compact_long_ledger_capped(tmp_path):
    proj = make_project(tmp_path)
    body = "\n".join(f"- decision {i}: keep helper {i} because reasons" for i in range(400))
    write_ledger(proj, "## Goal\nBig task\n\n" + body + "\nFINAL-UNIQUE-TAIL\n")
    text = context_of(run_hook(tmp_path, payload(proj, "compact")))
    assert TRUNCATION_MARKER in text
    assert "Big task" in text  # head kept
    assert "FINAL-UNIQUE-TAIL" not in text  # tail dropped
    inner = text.split("---\n", 1)[1].rsplit("\n---", 1)[0]
    assert len(inner) <= 6000


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
