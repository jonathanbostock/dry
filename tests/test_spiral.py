"""Tests for dry 0.4.0: window table + overrun failsafe (dry_gate), compaction
spiral detection (_common, dry_watch, dry_rehydrate), bounded snapshots and
the compaction counter (dry_checkpoint), handoff advisories (dry_watch).

Ground truth: session bc51b3a2 on crab-factory (2026-11-03..10) compacted 582
times; after each compaction the context was already ~121k of the ~140k the
harness compacted at, and on a 200k-class model the gate (assuming 1M) never
forced, so the API rejected the prompt ("Prompt is too long") twice.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _common  # noqa: E402


def usage_line(tokens: int, model: str = "claude-fable-5-2") -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"model": model, "usage": {
            "input_tokens": tokens, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "output_tokens": 0}},
    })


def boundary_line(pre: int, post: int) -> str:
    return json.dumps({
        "type": "system", "subtype": "compact_boundary",
        "compactMetadata": {"trigger": "auto", "preTokens": pre, "postTokens": post},
    })


def write_transcript(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n")
    return path


def make_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / ".claude" / "dry").mkdir(parents=True)
    return proj


def run(script: str, payload: dict, tmp_path: Path, **env_extra) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env.pop("DRY_GATE", None)
    env.update(env_extra)
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)], input=json.dumps(payload),
        capture_output=True, text=True, env=env, timeout=60,
    )


def payload(proj: Path, transcript: Path, event: str, **extra) -> dict:
    d = {"hook_event_name": event, "session_id": "spiral-test-session",
         "cwd": str(proj), "transcript_path": str(transcript)}
    d.update(extra)
    return d


def context_of(res: subprocess.CompletedProcess) -> str:
    if not res.stdout.strip():
        return ""
    return json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]


# ------------------------------------------------------------------ _common

def test_real_window_defaults_to_200k():
    assert _common.real_window("claude-fable-5-2") == 200_000
    assert _common.real_window("claude-opus-5-5") == 200_000
    assert _common.real_window(None) == 200_000
    assert _common.real_window("claude-opus-5-5[1m]") == 1_000_000
    assert _common.real_window("claude-fable-5-2", {"window_tokens": 180_000}) == 180_000


def test_compaction_epochs_and_spiral(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", [
        usage_line(130_000), boundary_line(140_000, 50_000), usage_line(121_000),
        usage_line(125_000), boundary_line(141_000, 49_000),
    ])
    epochs = _common.compaction_epochs(str(t))
    assert [e["pre"] for e in epochs] == [140_000, 141_000]
    assert epochs[0]["baseline"] == 121_000 and epochs[1]["baseline"] is None
    assert _common.spiral_epoch(epochs, 0.75)["pre"] == 140_000  # newest measurable epoch
    healthy = write_transcript(tmp_path / "h.jsonl", [
        boundary_line(140_000, 50_000), usage_line(70_000)])
    assert _common.spiral_epoch(_common.compaction_epochs(str(healthy)), 0.75) is None
    assert _common.spiral_epoch([], 0.75) is None


# --------------------------------------------------------------- dry_gate

def test_gate_overrun_failsafe(tmp_path):
    proj = make_project(tmp_path)
    pending = proj / ".claude" / "dry" / "compact-pending"
    t = write_transcript(tmp_path / "t.jsonl", [usage_line(150_000)])
    p = payload(proj, t, "PreCompact", trigger="auto")
    res = run("dry_gate.py", p, tmp_path, DRY_GATE="1")
    assert res.returncode == 2
    assert "attempt_tokens=150000" in pending.read_text()
    write_transcript(t, [usage_line(160_000)])  # +10k: still deferred
    assert run("dry_gate.py", p, tmp_path, DRY_GATE="1").returncode == 2
    write_transcript(t, [usage_line(171_000)])  # +21k > 20k: forced
    res = run("dry_gate.py", p, tmp_path, DRY_GATE="1")
    assert res.returncode == 0
    released = (proj / ".claude" / "dry" / "compact-released").read_text().split()
    assert released[0] == "overrun" and released[1] == "21000"
    assert not pending.exists()


def test_gate_window_failsafe_uses_200k_for_fable(tmp_path):
    proj = make_project(tmp_path)
    t = write_transcript(tmp_path / "t.jsonl", [usage_line(185_000, "claude-fable-5-2")])
    res = run("dry_gate.py", payload(proj, t, "PreCompact", trigger="auto"), tmp_path, DRY_GATE="1")
    assert res.returncode == 0
    assert (proj / ".claude" / "dry" / "compact-released").read_text().startswith("failsafe")


# -------------------------------------------------------------- dry_watch

def test_watch_spiral_notice_replaces_release_instruction(tmp_path):
    proj = make_project(tmp_path)
    (proj / ".claude" / "dry" / "compact-pending").write_text("pending\nattempt_tokens=140000\n")
    t = write_transcript(tmp_path / "t.jsonl", [boundary_line(140_000, 50_000), usage_line(121_000)])
    text = context_of(run("dry_watch.py", payload(proj, t, "UserPromptSubmit"), tmp_path, DRY_GATE="1"))
    assert "COMPACTION SPIRAL" in text and "do NOT release" in text and "/clear" in text
    assert "121,000" in text and "140,000" in text
    assert "touch" not in text.split("COMPACTION SPIRAL")[1].split("failsafe")[0]


def test_watch_pending_notice_unchanged_when_healthy(tmp_path):
    proj = make_project(tmp_path)
    (proj / ".claude" / "dry" / "compact-pending").write_text("pending\nattempt_tokens=140000\n")
    t = write_transcript(tmp_path / "t.jsonl", [boundary_line(140_000, 50_000), usage_line(70_000)])
    p = payload(proj, t, "UserPromptSubmit", session_id="spiral-test-healthy")  # own state: no dedupe
    text = context_of(run("dry_watch.py", p, tmp_path, DRY_GATE="1"))
    assert "gated auto-compact is pending" in text and "SPIRAL" not in text


def test_handoff_advisory_after_n_compactions(tmp_path):
    proj = make_project(tmp_path)
    t = write_transcript(tmp_path / "t.jsonl", [usage_line(30_000)])
    sid = f"spiral-handoff-{time.time_ns()}"  # dry state lives outside tmp_path: fresh id per run
    for _ in range(2):
        res = run("dry_checkpoint.py", payload(proj, t, "PostCompact", trigger="auto",
                  compact_summary="s", session_id=sid), tmp_path, DRY_HANDOFF_COMPACTIONS="2")
        assert res.returncode == 0
    text = context_of(run("dry_watch.py", payload(proj, t, "UserPromptSubmit", session_id=sid),
                          tmp_path, DRY_HANDOFF_COMPACTIONS="2"))
    assert "compacted 2 times" in text and "/clear" in text
    # once only
    text2 = context_of(run("dry_watch.py", payload(proj, t, "UserPromptSubmit", session_id=sid),
                           tmp_path, DRY_HANDOFF_COMPACTIONS="2"))
    assert "compacted" not in text2


# --------------------------------------------------------- dry_checkpoint

def test_snapshot_is_tail_bounded_and_tmp_debris_removed(tmp_path):
    proj = make_project(tmp_path)
    snapdir = proj / ".claude" / "dry" / "snapshots"
    snapdir.mkdir()
    stale = snapdir / "old-auto.jsonl.gz.tmp999"
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 3600, time.time() - 3600))
    lines = [usage_line(1_000 + i) for i in range(3_000)]  # ~400 KB
    t = write_transcript(tmp_path / "t.jsonl", lines)
    res = run("dry_checkpoint.py", payload(proj, t, "PreCompact", trigger="manual"), tmp_path,
              DRY_SNAPSHOT_MAX_BYTES="100000")
    assert res.returncode == 0
    assert not stale.exists()
    snaps = list(snapdir.glob("*.jsonl.gz"))
    assert len(snaps) == 1 and snaps[0].name.endswith("-manual-tail.jsonl.gz")
    data = gzip.decompress(snaps[0].read_bytes())
    assert len(data) <= 100_000
    first, _, _ = data.partition(b"\n")
    assert json.loads(first)["type"] == "assistant"  # starts on a line boundary
    assert data.endswith(usage_line(3_999).encode() + b"\n")


def test_snapshot_total_size_prune_keeps_newest(tmp_path):
    proj = make_project(tmp_path)
    snapdir = proj / ".claude" / "dry" / "snapshots"
    snapdir.mkdir()
    for i in range(3):
        (snapdir / f"2026010{i}T000000Z-x-auto.jsonl.gz").write_bytes(os.urandom(600_000))
    t = write_transcript(tmp_path / "t.jsonl", [usage_line(5)])
    run("dry_checkpoint.py", payload(proj, t, "PreCompact", trigger="manual"), tmp_path,
        DRY_SNAPSHOTS_MAX_TOTAL_MB="1", DRY_SNAPSHOT_MIN_INTERVAL_SECONDS="0")
    names = sorted(p.name for p in snapdir.glob("*.jsonl.gz"))
    assert names[-1].endswith("-manual.jsonl.gz")
    assert sum(p.stat().st_size for p in snapdir.glob("*.jsonl.gz")) <= 1024 * 1024 + 5_000


# ---------------------------------------------------------- dry_rehydrate

def test_rehydrate_warns_about_spiral(tmp_path):
    proj = make_project(tmp_path)
    (proj / ".claude" / "dry" / "ledger.md").write_text("# L\n\n## Goal\n- do things\n\n## Now\n- step\n")
    t = write_transcript(tmp_path / "t.jsonl", [
        boundary_line(140_000, 50_000), usage_line(121_000), boundary_line(141_000, 49_000)])
    text = context_of(run("dry_rehydrate.py", payload(proj, t, "SessionStart", source="compact"), tmp_path))
    assert "COMPACTION SPIRAL" in text and "/clear" in text
    healthy = write_transcript(tmp_path / "h.jsonl", [
        boundary_line(140_000, 50_000), usage_line(70_000), boundary_line(141_000, 49_000)])
    text = context_of(run("dry_rehydrate.py", payload(proj, healthy, "SessionStart", source="compact"), tmp_path))
    assert "SPIRAL" not in text and "do things" in text
