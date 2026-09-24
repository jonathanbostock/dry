"""Tests for the self-compaction gate (dry_gate.py) and its integrations:
the dry_watch pending notice and the dry_checkpoint snapshot throttle.

Live ground truth (2026-08-23 probe): blocked proactive auto-compacts RETRY
(five PreCompact(auto) attempts observed in one session), so exit-2 deferral
plus flag release is a viable control loop. Fail-open direction for the gate
is ALLOW: on any doubt it must exit 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

GATE_ON = {"DRY_GATE": "1"}


def usage_line(tokens: int, model: str = "claude-fable-5") -> str:
    return json.dumps({
        "type": "assistant",
        "message": {
            "model": model,
            "usage": {
                "input_tokens": tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    })


def make_transcript(tmp_path: Path, tokens: int, model: str = "claude-fable-5") -> Path:
    t = tmp_path / "transcript.jsonl"
    t.write_text(usage_line(tokens, model) + "\n")
    return t


def make_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / ".claude" / "dry").mkdir(parents=True)
    return proj


def payload(proj: Path, transcript, trigger: str = "auto", event: str = "PreCompact") -> dict:
    return {
        "hook_event_name": event,
        "session_id": "gate-test-session",
        "cwd": str(proj),
        "transcript_path": str(transcript),
        "trigger": trigger,
        "custom_instructions": "",
    }


def run_script(script: str, data: dict, tmp_path: Path, env_extra: dict | None = None):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("DRY_GATE", None)
    env.pop("DRY_GATE_ENABLED", None)
    env["TMPDIR"] = str(tmp_path / "tmp")
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        input=json.dumps(data).encode(),
        capture_output=True,
        env=env,
        timeout=30,
    )


def dry_dir(proj: Path) -> Path:
    return proj / ".claude" / "dry"


# ---------------------------------------------------------------- gate core

def test_gate_disabled_by_default(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path)
    assert proc.returncode == 0 and proc.stdout == b""
    assert not (dry_dir(proj) / "compact-pending").exists()


def test_gate_manual_always_allowed(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    proc = run_script("dry_gate.py", payload(proj, t, trigger="manual"), tmp_path, GATE_ON)
    assert proc.returncode == 0
    assert not (dry_dir(proj) / "compact-pending").exists()


def test_gate_blocks_auto_without_flag(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)  # far from the 1M failsafe line
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 2
    assert b"deferring auto-compact" in proc.stderr
    assert (dry_dir(proj) / "compact-pending").is_file()


def test_gate_reblock_preserves_pending_mtime(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    marker = dry_dir(proj) / "compact-pending"
    old = time.time() - 500
    os.utime(marker, (old, old))
    run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert abs(marker.stat().st_mtime - old) < 2  # not rewritten on re-block


def test_gate_flag_releases_and_consumes(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    (dry_dir(proj) / "compact-ok").touch()
    (dry_dir(proj) / "compact-pending").write_text("pending\n")
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 0
    assert not (dry_dir(proj) / "compact-ok").exists()
    assert not (dry_dir(proj) / "compact-pending").exists()


def test_gate_stale_flag_ignored_but_consumed(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    flag = dry_dir(proj) / "compact-ok"
    flag.touch()
    old = time.time() - 2 * 3600  # > gate_flag_max_age_minutes (60)
    os.utime(flag, (old, old))
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 2
    assert not flag.exists()
    assert (dry_dir(proj) / "compact-pending").is_file()


def test_gate_failsafe_releases_near_1m_limit(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 950_000)  # >= 0.9 * 1M
    (dry_dir(proj) / "compact-pending").write_text("pending\n")
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 0
    assert not (dry_dir(proj) / "compact-pending").exists()


def test_gate_failsafe_uses_200k_window_for_haiku(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 190_000, model="claude-haiku-4-5")  # >= 0.9 * 200k
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 0


def test_gate_blind_allows(tmp_path):
    proj = make_project(tmp_path)
    proc = run_script(
        "dry_gate.py", payload(proj, tmp_path / "nonexistent.jsonl"), tmp_path, GATE_ON
    )
    assert proc.returncode == 0


def test_gate_env_alias_and_config_key(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, {"DRY_GATE_ENABLED": "1"})
    assert proc.returncode == 2
    (dry_dir(proj) / "compact-pending").unlink()
    (dry_dir(proj) / "config.json").write_text(json.dumps({"gate_enabled": True}))
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path)
    assert proc.returncode == 2


def test_gate_malformed_stdin_allows(tmp_path):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["TMPDIR"] = str(tmp_path)
    env["DRY_GATE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "dry_gate.py")],
        input=b"{not json", capture_output=True, env=env, timeout=30,
    )
    assert proc.returncode == 0 and proc.stdout == b""


# ------------------------------------------------- watch pending integration

def watch_payload(proj: Path, transcript) -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "gate-watch-session",
        "cwd": str(proj),
        "transcript_path": str(transcript),
        "tool_name": "Bash",
        "tool_response": {"stdout": "ok", "stderr": ""},
    }


def test_watch_emits_pending_notice_once(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 10_000)
    (dry_dir(proj) / "compact-pending").write_text("pending\n")
    first = run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    assert first.returncode == 0
    body = json.loads(first.stdout.decode())
    text = body["hookSpecificOutput"]["additionalContext"]
    assert "gated auto-compact is pending" in text
    assert f"touch {dry_dir(proj) / 'compact-ok'}" in text  # absolute path: cwd-proof
    assert "nothing to ask or tell the user" in text
    assert "recommend" not in text and "propose" not in text
    second = run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    assert second.stdout == b""  # same marker mtime -> notified once


def test_watch_renotices_new_gate_episode(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 10_000)
    marker = dry_dir(proj) / "compact-pending"
    marker.write_text("pending\n")
    run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    marker.unlink()
    gone = run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    assert gone.stdout == b""
    marker.write_text("pending again\n")
    future = time.time() + 5  # ensure a different integer mtime
    os.utime(marker, (future, future))
    again = run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    assert b"gated auto-compact is pending" in again.stdout


# --------------------------------------------- checkpoint snapshot throttle

def test_checkpoint_throttles_fresh_snapshot(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 10_000)
    sd = dry_dir(proj) / "snapshots"
    sd.mkdir()
    (sd / "20990101T000000Z-old-auto.jsonl.gz").write_bytes(b"x")  # fresh mtime
    proc = run_script("dry_checkpoint.py", payload(proj, t, trigger="manual"), tmp_path)
    assert proc.returncode == 0
    assert len(list(sd.glob("*.jsonl.gz"))) == 1  # skipped: one is <60s old
    proc = run_script(
        "dry_checkpoint.py", payload(proj, t, trigger="manual"), tmp_path,
        {"DRY_SNAPSHOT_MIN_INTERVAL_SECONDS": "0"},
    )
    assert proc.returncode == 0
    assert len(list(sd.glob("*.jsonl.gz"))) == 2  # override: snapshot written


# ------------------------------------------- released marker (for dry_checkpoint)

def test_gate_release_writes_released_marker(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    (dry_dir(proj) / "compact-ok").write_text("")
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 0
    assert (dry_dir(proj) / "compact-released").read_text().strip() == "flag"
    assert not (dry_dir(proj) / "compact-ok").exists()


def test_gate_failsafe_writes_released_marker(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 950_000)  # past 0.9 x 1M
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 0
    assert (dry_dir(proj) / "compact-released").read_text().strip() == "failsafe"


def test_gate_block_leaves_no_released_marker(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 2
    assert not (dry_dir(proj) / "compact-released").exists()


# ------------------------------------------------------- pending reminder

def test_watch_pending_reminder_after_interval(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 10_000)
    marker = dry_dir(proj) / "compact-pending"
    marker.write_text("pending\n")
    old = time.time() - 45 * 60
    os.utime(marker, (old, old))
    first = run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    assert b"gated auto-compact is pending" in first.stdout
    quiet = run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    assert quiet.stdout == b""  # reminder interval not yet elapsed
    # age the last-reminded clock past the interval
    state_path = tmp_path / "tmp" / "claude-dry" / "gate-watch-session" / "state.json"
    state = json.loads(state_path.read_text())
    state["watch"]["pending_reminded"] = time.time() - 31 * 60
    state_path.write_text(json.dumps(state))
    reminder = run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    body = json.loads(reminder.stdout.decode())["hookSpecificOutput"]["additionalContext"]
    assert body.startswith("[dry] Reminder: a gated auto-compact has been pending for ~45 min")
    assert "compact-ok" in body and "Nothing to tell the user" in body
    again = run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    assert again.stdout == b""  # reminded once per interval


def test_watch_pending_reminder_disabled_by_config(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 10_000)
    (dry_dir(proj) / "compact-pending").write_text("pending\n")
    env = dict(GATE_ON, DRY_GATE_REMIND_MINUTES="0")
    run_script("dry_watch.py", watch_payload(proj, t), tmp_path, env)
    state_path = tmp_path / "tmp" / "claude-dry" / "gate-watch-session" / "state.json"
    state = json.loads(state_path.read_text())
    state["watch"]["pending_reminded"] = time.time() - 3 * 3600
    state_path.write_text(json.dumps(state))
    assert run_script("dry_watch.py", watch_payload(proj, t), tmp_path, env).stdout == b""


# ------------------------------------ manual, timeout, blind, stale, anchoring

def test_gate_manual_clears_pending(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    (dry_dir(proj) / "compact-pending").write_text("pending\n")
    proc = run_script("dry_gate.py", payload(proj, t, trigger="manual"), tmp_path, GATE_ON)
    assert proc.returncode == 0
    assert not (dry_dir(proj) / "compact-pending").exists()
    assert not (dry_dir(proj) / "compact-released").exists()  # manual: nothing to label


def test_gate_timeout_backstop_releases_long_pending(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    marker = dry_dir(proj) / "compact-pending"
    marker.write_text("pending\n")
    old = time.time() - 95 * 60  # > gate_max_pending_minutes (90)
    os.utime(marker, (old, old))
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 0
    assert not marker.exists()
    kind, mins = (dry_dir(proj) / "compact-released").read_text().split()
    assert kind == "timeout" and 94 <= int(mins) <= 96


def test_gate_timeout_backstop_disabled_by_zero(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    marker = dry_dir(proj) / "compact-pending"
    marker.write_text("pending\n")
    old = time.time() - 10 * 3600
    os.utime(marker, (old, old))
    env = dict(GATE_ON, DRY_GATE_MAX_PENDING_MINUTES="0")
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, env)
    assert proc.returncode == 2
    assert abs(marker.stat().st_mtime - old) < 2


def test_gate_stale_pending_debris_restarts_episode(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    marker = dry_dir(proj) / "compact-pending"
    marker.write_text("pending\n")
    old = time.time() - 3 * 86400  # left behind by a session days ago
    os.utime(marker, (old, old))
    proc = run_script("dry_gate.py", payload(proj, t), tmp_path, GATE_ON)
    assert proc.returncode == 2  # not a timeout release: fresh block
    assert time.time() - marker.stat().st_mtime < 60  # re-created, so dry_watch re-notifies
    assert not (dry_dir(proj) / "compact-released").exists()


def test_gate_blind_writes_blind_marker(tmp_path):
    proj = make_project(tmp_path)
    proc = run_script(
        "dry_gate.py", payload(proj, tmp_path / "nonexistent.jsonl"), tmp_path, GATE_ON
    )
    assert proc.returncode == 0
    assert (dry_dir(proj) / "compact-released").read_text().strip() == "blind"


def test_gate_honours_flag_touched_in_drifted_cwd(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    sub = proj / "experiments" / "run1"
    (sub / ".claude" / "dry").mkdir(parents=True)
    (sub / ".claude" / "dry" / "compact-ok").touch()  # agent cd'd into sub, touched relatively
    data = payload(proj, t)
    data["cwd"] = str(sub)
    env = dict(GATE_ON, CLAUDE_PROJECT_DIR=str(proj))
    proc = run_script("dry_gate.py", data, tmp_path, env)
    assert proc.returncode == 0
    assert not (sub / ".claude" / "dry" / "compact-ok").exists()
    assert (dry_dir(proj) / "compact-released").read_text().strip() == "flag"


def test_gate_markers_anchor_on_project_dir(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 150_000)
    sub = proj / "worktree"
    sub.mkdir()
    data = payload(proj, t)
    data["cwd"] = str(sub)
    env = dict(GATE_ON, CLAUDE_PROJECT_DIR=str(proj))
    proc = run_script("dry_gate.py", data, tmp_path, env)
    assert proc.returncode == 2
    assert (dry_dir(proj) / "compact-pending").is_file()
    assert not (sub / ".claude").exists()
    assert str(dry_dir(proj) / "compact-ok") in proc.stderr.decode()  # absolute release path


def test_watch_silent_for_subagent_calls(tmp_path):
    proj = make_project(tmp_path)
    t = make_transcript(tmp_path, 10_000)
    (dry_dir(proj) / "compact-pending").write_text("pending\n")
    data = watch_payload(proj, t)
    data["agent_id"] = "a78457c342d80f771"
    data["agent_type"] = "general-purpose"
    proc = run_script("dry_watch.py", data, tmp_path)
    assert proc.returncode == 0 and proc.stdout == b""
    main = run_script("dry_watch.py", watch_payload(proj, t), tmp_path)
    assert b"gated auto-compact is pending" in main.stdout  # the parent still gets it
