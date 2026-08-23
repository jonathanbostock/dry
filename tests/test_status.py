"""Tests for scripts/dry_status.py — run as a subprocess CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "dry_status.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROBE_TRANSCRIPT = FIXTURES / "probe-transcript.jsonl"


def munge(cwd: Path) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", str(cwd.resolve()))


def run_status(args: list[str], tmp_path: Path, env_extra: dict | None = None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config"),
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=b"",
        capture_output=True,
        env=env,
        timeout=60,
    )


def write_transcript(path: Path, tokens: int) -> None:
    """Minimal JSONL transcript shaped like the real fixture (see test_watch)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    usage = {
        "input_tokens": tokens - 30,
        "cache_creation_input_tokens": 12,
        "cache_read_input_tokens": 10,
        "output_tokens": 8,
    }
    user = {"parentUuid": None, "isSidechain": False, "type": "user", "uuid": "u-1",
            "timestamp": "2026-08-23T12:00:00.000Z", "sessionId": "s",
            "message": {"role": "user", "content": "go"}}
    assistant = {"parentUuid": "u-1", "isSidechain": False, "type": "assistant",
                 "uuid": "a-1", "timestamp": "2026-08-23T12:00:01.000Z",
                 "sessionId": "s", "requestId": "req_1",
                 "message": {"model": "claude-fable-5", "id": "msg_1",
                             "type": "message", "role": "assistant",
                             "content": [{"type": "text", "text": "ok"}],
                             "stop_reason": "end_turn", "usage": usage}}
    path.write_text(json.dumps(user) + "\n" + json.dumps(assistant) + "\n")


def project_transcripts_dir(tmp_path: Path, cwd: Path) -> Path:
    return tmp_path / "claude-config" / "projects" / munge(cwd)


def test_full_report(tmp_path):
    cwd = tmp_path / "proj"
    dry = cwd / ".claude" / "dry"
    (dry / "snapshots").mkdir(parents=True)
    (dry / "archive" / "abcd1234").mkdir(parents=True)
    (dry / "ledger.md").write_text(
        "## Goal\nShip the dry plugin end to end\n\n## Now\nBuilding status tests\n"
    )
    old = dry / "snapshots" / "20260823T100000Z-aaaa1111-manual.jsonl.gz"
    new = dry / "snapshots" / "20260823T110000Z-bbbb2222-auto.jsonl.gz"
    old.write_bytes(b"x" * 100)
    new.write_bytes(b"y" * 100)
    os.utime(old, (1_000_000_000, 1_000_000_000))
    for i, size in enumerate((1024, 1024)):
        (dry / "archive" / "abcd1234" / f"{i:03d}-Bash.txt").write_text("z" * size)

    sid = "sess-status-1"
    write_transcript(project_transcripts_dir(tmp_path, cwd) / f"{sid}.jsonl", 120_000)
    state_dir = tmp_path / "claude-dry" / sid
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        json.dumps({"watch": {"last_check_wall": 0, "calls_since": 0,
                              "last_band": 0, "top": [[71_882, "Read"], [36_475, "Bash"]]}})
    )

    result = run_status(["--cwd", str(cwd)], tmp_path, {"CLAUDE_SESSION_ID": sid})
    assert result.returncode == 0
    out = result.stdout.decode()
    assert not out.lstrip().startswith("{")  # plain text, not JSON
    assert "~120,000 tokens" in out
    assert "~60% of 200,000" in out
    assert "band: advisory" in out
    assert "Read 72k, Bash 36k chars" in out
    assert "goal: Ship the dry plugin end to end" in out
    assert "5 lines" in out
    assert "s old" in out  # freshly written ledger, seconds-scale age
    assert "2 (newest: 20260823T110000Z-bbbb2222-auto.jsonl.gz)" in out
    assert "2 files, 2.0 KB" in out
    assert "action:" in out and "ledger" in out


def test_bare_report_renders_unknowns(tmp_path):
    cwd = tmp_path / "empty-proj"
    cwd.mkdir()
    result = run_status(["--cwd", str(cwd)], tmp_path)
    assert result.returncode == 0
    assert result.stderr == b""
    out = result.stdout.decode()
    assert "unknown (no transcript found)" in out
    assert "top hogs:  unknown" in out
    assert "ledger:    none" in out
    assert "snapshots: none" in out
    assert "archive:   none" in out
    assert "action:    unknown" in out


def test_explicit_transcript_path(tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    result = run_status(
        ["--cwd", str(cwd), "--transcript", str(PROBE_TRANSCRIPT)], tmp_path
    )
    assert result.returncode == 0
    out = result.stdout.decode()
    assert "~64,677 tokens" in out
    assert "~32% of 200,000" in out
    assert "band: ok" in out
    assert "none —" in out  # ok-band action line


def test_session_discovery_via_config_dir(tmp_path):
    cwd = tmp_path / "my.project"  # dots munge to dashes
    cwd.mkdir()
    sid = "abc-123"
    write_transcript(project_transcripts_dir(tmp_path, cwd) / f"{sid}.jsonl", 150_000)
    result = run_status(["--cwd", str(cwd), "--session", sid], tmp_path)
    out = result.stdout.decode()
    assert "~150,000 tokens" in out
    assert "band: degradation" in out


def test_newest_mtime_discovery_and_critical_band(tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    proj_dir = project_transcripts_dir(tmp_path, cwd)
    write_transcript(proj_dir / "older-session.jsonl", 20_000)
    write_transcript(proj_dir / "newer-session.jsonl", 172_000)  # 86%
    os.utime(proj_dir / "older-session.jsonl", (1_000_000_000, 1_000_000_000))
    result = run_status(["--cwd", str(cwd)], tmp_path)
    out = result.stdout.decode()
    assert "~172,000 tokens" in out
    assert "band: critical" in out
    assert "checkpoint NOW" in out


def test_env_session_id_beats_newest(tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    proj_dir = project_transcripts_dir(tmp_path, cwd)
    write_transcript(proj_dir / "other.jsonl", 20_000)
    write_transcript(proj_dir / "mine.jsonl", 120_000)
    os.utime(proj_dir / "mine.jsonl", (1_000_000_000, 1_000_000_000))  # older mtime
    result = run_status(["--cwd", str(cwd)], tmp_path, {"CLAUDE_SESSION_ID": "mine"})
    assert "~120,000 tokens" in result.stdout.decode()


def test_nonexistent_cwd_exits_zero(tmp_path):
    result = run_status(["--cwd", str(tmp_path / "does" / "not" / "exist")], tmp_path)
    assert result.returncode == 0
    out = result.stdout.decode()
    assert "unknown" in out
    assert "Traceback" not in result.stderr.decode()


def test_bad_args_exit_zero(tmp_path):
    result = run_status(["--no-such-flag"], tmp_path)
    assert result.returncode == 0
    assert result.stdout == b""


def test_ledger_without_goal_and_empty_state(tmp_path):
    cwd = tmp_path / "proj"
    dry = cwd / ".claude" / "dry"
    dry.mkdir(parents=True)
    (dry / "ledger.md").write_text("just notes\nno headings here\n")
    sid = "sess-empty"
    write_transcript(project_transcripts_dir(tmp_path, cwd) / f"{sid}.jsonl", 20_000)
    result = run_status(["--cwd", str(cwd), "--session", sid], tmp_path)
    out = result.stdout.decode()
    assert "goal: unknown" in out
    assert "2 lines" in out
    assert "none tracked yet" in out  # sid known, no watch state recorded
    assert "band: ok" in out
