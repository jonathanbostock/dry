"""Subprocess tests for scripts/dry_checkpoint.py (PreCompact + PostCompact).

Every run must exit 0 with empty stdout — the checkpoint hook is
side-effects only and must never block compaction.
"""

from __future__ import annotations

import gzip
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dry_checkpoint.py"
SESSION_ID = "a8ba0f4e-438f-4a93-853e-61bc793d52f5"
SID8 = "a8ba0f4e"
SUMMARY = "## Conversation summary\n- built the checkpoint hook\n- tests green\n"


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


def make_transcript(tmp_path: Path, lines: int = 50) -> Path:
    path = tmp_path / "transcript.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for i in range(lines):
            f.write(json.dumps({"type": "user", "uuid": f"u{i:04d}", "text": "x" * 40}) + "\n")
    return path


def snapdir(proj: Path) -> Path:
    return proj / ".claude" / "dry" / "snapshots"


def pre_compact_payload(proj: Path, transcript, trigger: str = "manual") -> dict:
    return {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(proj),
        "hook_event_name": "PreCompact",
        "trigger": trigger,
        "custom_instructions": "",
        "current_token_count": 123_456,
    }


def post_compact_payload(proj: Path, trigger: str = "manual", summary=SUMMARY) -> dict:
    return {
        "session_id": SESSION_ID,
        "cwd": str(proj),
        "hook_event_name": "PostCompact",
        "trigger": trigger,
        "compact_summary": summary,
    }


# --- PreCompact ---------------------------------------------------------


def test_precompact_manual_snapshot(tmp_path):
    proj = make_project(tmp_path)
    transcript = make_transcript(tmp_path)
    proc = run_hook(tmp_path, pre_compact_payload(proj, transcript))
    assert proc.returncode == 0
    assert proc.stdout == b""
    snaps = list(snapdir(proj).glob("*.jsonl.gz"))
    assert len(snaps) == 1
    snap = snaps[0]
    assert SID8 in snap.name and snap.name.endswith("-manual.jsonl.gz")
    assert gzip.decompress(snap.read_bytes()) == transcript.read_bytes()
    assert stat.S_IMODE(snap.stat().st_mode) == 0o600
    # manual trigger leaves the ledger alone
    assert not (proj / ".claude" / "dry" / "ledger.md").exists()


def test_precompact_auto_leaves_ledger_alone(tmp_path):
    """PreCompact fires on every gate-blocked attempt too, so it must not
    write "a compaction happened" into the ledger (regression: 198 ⚠ lines
    in one gated project's ledger)."""
    proj = make_project(tmp_path)
    transcript = make_transcript(tmp_path)
    proc = run_hook(tmp_path, pre_compact_payload(proj, transcript, trigger="auto"))
    assert proc.returncode == 0 and proc.stdout == b""
    assert len(list(snapdir(proj).glob("*-auto.jsonl.gz"))) == 1
    assert not (proj / ".claude" / "dry" / "ledger.md").exists()


def _ledger(proj: Path) -> Path:
    return proj / ".claude" / "dry" / "ledger.md"


def test_postcompact_auto_appends_mid_task_warning(tmp_path):
    proj = make_project(tmp_path)
    transcript = make_transcript(tmp_path)
    run_hook(tmp_path, pre_compact_payload(proj, transcript, trigger="auto"))
    proc = run_hook(tmp_path, post_compact_payload(proj, trigger="auto"))
    assert proc.returncode == 0 and proc.stdout == b""
    content = _ledger(proj).read_text(encoding="utf-8")
    assert content.startswith("# dry ledger\n\n")
    assert "⚠" in content and "auto-compact fired mid-task" in content
    snaps = list(snapdir(proj).glob("*-auto.jsonl.gz"))
    assert len(snaps) == 1 and os.path.relpath(snaps[0], proj) in content
    # a second compaction appends without duplicating the header
    run_hook(tmp_path, post_compact_payload(proj, trigger="auto"))
    content = _ledger(proj).read_text(encoding="utf-8")
    assert content.count("# dry ledger") == 1
    assert content.count("auto-compact fired mid-task") == 2


def test_postcompact_manual_leaves_ledger_alone(tmp_path):
    proj = make_project(tmp_path)
    proc = run_hook(tmp_path, post_compact_payload(proj, trigger="manual"))
    assert proc.returncode == 0 and proc.stdout == b""
    assert not _ledger(proj).exists()


def test_postcompact_released_marker_gives_calm_note(tmp_path):
    proj = make_project(tmp_path)
    dry = proj / ".claude" / "dry"
    dry.mkdir(parents=True)
    (dry / "compact-released").write_text("flag\n")
    run_hook(tmp_path, post_compact_payload(proj, trigger="auto"))
    content = _ledger(proj).read_text(encoding="utf-8")
    assert "✓" in content and "released at a boundary (gated run)" in content
    assert "⚠" not in content and "mid-task" not in content
    assert not (dry / "compact-released").exists()  # consumed


def test_postcompact_failsafe_marker_gives_warning(tmp_path):
    proj = make_project(tmp_path)
    dry = proj / ".claude" / "dry"
    dry.mkdir(parents=True)
    (dry / "compact-released").write_text("failsafe\n")
    run_hook(tmp_path, post_compact_payload(proj, trigger="auto"))
    content = _ledger(proj).read_text(encoding="utf-8")
    assert "⚠" in content and "forced by the gate's failsafe" in content
    assert not (dry / "compact-released").exists()


def test_postcompact_stale_released_marker_ignored(tmp_path):
    proj = make_project(tmp_path)
    dry = proj / ".claude" / "dry"
    dry.mkdir(parents=True)
    marker = dry / "compact-released"
    marker.write_text("flag\n")
    old = marker.stat().st_mtime - 3600
    os.utime(marker, (old, old))
    run_hook(tmp_path, post_compact_payload(proj, trigger="auto"))
    content = _ledger(proj).read_text(encoding="utf-8")
    assert "auto-compact fired mid-task" in content and "released" not in content
    assert not marker.exists()  # still consumed so it cannot mislabel a later compaction


def test_postcompact_ledger_note_without_summary(tmp_path):
    proj = make_project(tmp_path)
    payload = post_compact_payload(proj, trigger="auto", summary="")
    proc = run_hook(tmp_path, payload)
    assert proc.returncode == 0 and proc.stdout == b""
    assert "auto-compact fired mid-task" in _ledger(proj).read_text(encoding="utf-8")


def test_prune_keeps_newest(tmp_path):
    proj = make_project(tmp_path)
    transcript = make_transcript(tmp_path)
    sd = snapdir(proj)
    sd.mkdir(parents=True)
    for i in range(12):
        name = f"20250101T{i:02d}0000Z-deadbeef-auto.jsonl.gz"
        (sd / name).write_bytes(gzip.compress(f"old {i}".encode()))
    proc = run_hook(
        tmp_path,
        pre_compact_payload(proj, transcript),
        extra_env={"DRY_SNAPSHOT_MIN_INTERVAL_SECONDS": "0"},
    )
    assert proc.returncode == 0
    remaining = sorted(p.name for p in sd.glob("*.jsonl.gz"))
    assert len(remaining) == 10
    assert remaining[-1].endswith("-manual.jsonl.gz")  # fresh snapshot survived
    for i in range(3):  # the three oldest are gone
        assert f"20250101T{i:02d}0000Z-deadbeef-auto.jsonl.gz" not in remaining


def test_prune_respects_config(tmp_path):
    proj = make_project(tmp_path)
    transcript = make_transcript(tmp_path)
    sd = snapdir(proj)
    sd.mkdir(parents=True)
    for i in range(4):
        (sd / f"20250101T{i:02d}0000Z-deadbeef-auto.jsonl.gz").write_bytes(b"x")
    proc = run_hook(
        tmp_path,
        pre_compact_payload(proj, transcript),
        extra_env={"DRY_SNAPSHOTS_KEEP": "2", "DRY_SNAPSHOT_MIN_INTERVAL_SECONDS": "0"},
    )
    assert proc.returncode == 0
    remaining = sorted(p.name for p in sd.glob("*.jsonl.gz"))
    assert len(remaining) == 2
    assert remaining[-1].endswith("-manual.jsonl.gz")


def test_precompact_missing_transcript_path(tmp_path):
    proj = make_project(tmp_path)
    payload = pre_compact_payload(proj, "unused", trigger="auto")
    del payload["transcript_path"]
    proc = run_hook(tmp_path, payload)
    assert proc.returncode == 0 and proc.stdout == b""
    sd = snapdir(proj)
    assert not sd.exists() or list(sd.glob("*.jsonl.gz")) == []
    # no snapshot to reference -> no ledger warning either
    assert not (proj / ".claude" / "dry" / "ledger.md").exists()


def test_precompact_nonexistent_transcript(tmp_path):
    proj = make_project(tmp_path)
    proc = run_hook(tmp_path, pre_compact_payload(proj, tmp_path / "missing.jsonl"))
    assert proc.returncode == 0 and proc.stdout == b""
    assert list(snapdir(proj).glob("*.jsonl.gz")) == []


# --- PostCompact --------------------------------------------------------


def test_postcompact_writes_summary(tmp_path):
    proj = make_project(tmp_path)
    proc = run_hook(tmp_path, post_compact_payload(proj))
    assert proc.returncode == 0 and proc.stdout == b""
    files = list(snapdir(proj).glob("*.md"))
    assert len(files) == 1
    f = files[0]
    assert f"-{SID8}-summary-manual.md" in f.name
    content = f.read_text(encoding="utf-8")
    header, body = content.split("\n\n", 1)
    assert body == SUMMARY  # verbatim
    lines = header.split("\n")
    assert len(lines) == 2  # two-line header: timestamp + trigger
    assert lines[0].startswith("saved: ")
    assert lines[1] == "trigger: manual"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_postcompact_empty_or_missing_summary_writes_nothing(tmp_path):
    proj = make_project(tmp_path)
    for summary in ("", 42, None):
        payload = post_compact_payload(proj, summary=summary)
        if summary is None:
            del payload["compact_summary"]
        proc = run_hook(tmp_path, payload)
        assert proc.returncode == 0 and proc.stdout == b""
    sd = snapdir(proj)
    assert not sd.exists() or list(sd.glob("*")) == []


def test_postcompact_prunes_md_separately(tmp_path):
    proj = make_project(tmp_path)
    sd = snapdir(proj)
    sd.mkdir(parents=True)
    for i in range(12):
        (sd / f"20250101T{i:02d}0000Z-deadbeef-summary-auto.md").write_text(f"old {i}")
    for i in range(3):
        (sd / f"20250101T{i:02d}0000Z-deadbeef-auto.jsonl.gz").write_bytes(b"x")
    proc = run_hook(tmp_path, post_compact_payload(proj))
    assert proc.returncode == 0
    assert len(list(sd.glob("*.md"))) == 10
    assert len(list(sd.glob("*.jsonl.gz"))) == 3  # untouched by the .md prune


# --- fail-open behaviour ------------------------------------------------


def test_disable_env_vars_silent(tmp_path):
    proj = make_project(tmp_path)
    transcript = make_transcript(tmp_path)
    for var in ("DRY_DISABLE", "DRY_DISABLE_CHECKPOINT"):
        proc = run_hook(tmp_path, pre_compact_payload(proj, transcript), extra_env={var: "1"})
        assert proc.returncode == 0 and proc.stdout == b""
    assert not (proj / ".claude").exists()


def test_malformed_stdin_exits_zero(tmp_path):
    for raw in (b"", b"not json", b"[1,2,3]", b'{"cwd": 42}', b'{"hook_event_name": "PreCompact"}'):
        proc = run_hook(tmp_path, raw=raw)
        assert proc.returncode == 0
        assert proc.stdout == b""


def test_cwd_is_a_file_exits_zero(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where a directory should be")
    transcript = make_transcript(tmp_path)
    payload = pre_compact_payload(blocker, transcript)
    proc = run_hook(tmp_path, payload)
    assert proc.returncode == 0 and proc.stdout == b""


def test_postcompact_timeout_and_blind_markers(tmp_path):
    proj = make_project(tmp_path)
    dry = proj / ".claude" / "dry"
    dry.mkdir(parents=True)
    (dry / "compact-released").write_text("timeout 95\n")
    run_hook(tmp_path, post_compact_payload(proj, trigger="auto"))
    content = _ledger(proj).read_text(encoding="utf-8")
    assert "⚠" in content and "pending 95 min without release" in content
    (dry / "compact-released").write_text("blind\n")
    run_hook(tmp_path, post_compact_payload(proj, trigger="auto"))
    content = _ledger(proj).read_text(encoding="utf-8")
    assert "ℹ" in content and "could not read the context size" in content


def test_precompact_gated_snapshot_interval_is_longer(tmp_path):
    proj = make_project(tmp_path)
    transcript = make_transcript(tmp_path)
    sd = snapdir(proj)
    sd.mkdir(parents=True)
    fresh = sd / "20990101T000000Z-old-auto.jsonl.gz"
    fresh.write_bytes(b"x")
    old = time.time() - 300  # 5 min: past the 60 s default, inside the 600 s gated interval
    os.utime(fresh, (old, old))
    run_hook(tmp_path, pre_compact_payload(proj, transcript, trigger="auto"), extra_env={"DRY_GATE": "1"})
    assert len(list(sd.glob("*.jsonl.gz"))) == 1  # throttled in gate mode
    run_hook(tmp_path, pre_compact_payload(proj, transcript, trigger="auto"))
    assert len(list(sd.glob("*.jsonl.gz"))) == 2  # not throttled without the gate
