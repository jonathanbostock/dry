"""Tests for scripts/dry_watch.py — run as a subprocess, the real hook contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "dry_watch.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROBE_TRANSCRIPT = FIXTURES / "probe-transcript.jsonl"
PAYLOADS = FIXTURES / "payloads"

sys.path.insert(0, str(REPO / "scripts"))
import _common  # noqa: E402


def run_hook(stdin_data, tmp_path: Path, env_extra: dict | None = None):
    """Run dry_watch.py as a subprocess; state lands under tmp_path via TMPDIR."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if env_extra:
        env.update(env_extra)
    if isinstance(stdin_data, dict):
        stdin_data = json.dumps(stdin_data).encode()
    elif isinstance(stdin_data, str):
        stdin_data = stdin_data.encode()
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_data,
        capture_output=True,
        env=env,
        timeout=60,
    )


def write_transcript(path: Path, tokens: int, sid: str = "synthetic") -> None:
    """Write a minimal JSONL transcript shaped like the real fixture.

    The scanner drops the first line of the tail (may be partial after a
    seek), so the assistant record must not be line 1. A trailing sidechain
    record with huge usage checks that only main-chain records count.
    """
    usage = {
        "input_tokens": tokens - 30,
        "cache_creation_input_tokens": 12,
        "cache_read_input_tokens": 10,
        "output_tokens": 8,
        "service_tier": "standard",
    }
    base = {
        "parentUuid": None,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/tmp/dry-probe",
        "sessionId": sid,
        "version": "2.1.241",
        "gitBranch": "HEAD",
    }
    user = dict(base, type="user", uuid="u-1", timestamp="2026-08-23T12:00:00.000Z",
                message={"role": "user", "content": "go"})
    assistant = dict(
        base, type="assistant", uuid="a-1", parentUuid="u-1",
        timestamp="2026-08-23T12:00:01.000Z", requestId="req_1",
        message={
            "model": "claude-fable-5", "id": "msg_1", "type": "message",
            "role": "assistant", "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn", "usage": usage,
        },
    )
    sidechain = dict(assistant, uuid="a-2", isSidechain=True)
    sidechain["message"] = dict(assistant["message"], usage=dict(usage, input_tokens=999_999))
    path.write_text("".join(json.dumps(r) + "\n" for r in (user, assistant, sidechain)))


def prompt_payload(cwd: Path, transcript: Path, sid: str) -> dict:
    return {
        "session_id": sid,
        "transcript_path": str(transcript),
        "cwd": str(cwd),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "continue",
        "permission_mode": "auto",
    }


def tool_payload(cwd: Path, transcript: Path, sid: str, fixture: str = "04-Bash.json") -> dict:
    data = json.loads((PAYLOADS / fixture).read_text())
    data.update(session_id=sid, transcript_path=str(transcript), cwd=str(cwd))
    return data


def read_state(tmp_path: Path, sid: str) -> dict:
    return json.loads((tmp_path / "claude-dry" / sid / "state.json").read_text())


def parse_output(result) -> str:
    """Assert the hook's JSON envelope and return additionalContext."""
    assert result.returncode == 0
    out = json.loads(result.stdout)
    hso = out["hookSpecificOutput"]
    assert set(hso) == {"hookEventName", "additionalContext"}
    return hso["additionalContext"]


def setup_session(tmp_path: Path, tokens: int):
    cwd = tmp_path / "proj"
    cwd.mkdir(exist_ok=True)
    transcript = tmp_path / "transcript.jsonl"
    write_transcript(transcript, tokens)
    return cwd, transcript


def test_band_crossing_up_emits_json(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)  # 60% of 200k
    result = run_hook(prompt_payload(cwd, transcript, "w1"), tmp_path)
    assert result.stderr == b""
    text = parse_output(result)
    envelope = json.loads(result.stdout)
    assert envelope["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert text.startswith("[dry] Context check: ~120,000 tokens")
    assert "~60% of your 200,000-token working budget" in text
    assert "none tracked yet" in text  # no hogs seen yet
    assert ".claude/dry/ledger.md" in text
    state = read_state(tmp_path, "w1")
    assert state["watch"]["last_band"] == 0
    assert state["watch"]["calls_since"] == 0


def test_same_band_no_refire(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    first = run_hook(prompt_payload(cwd, transcript, "w2"), tmp_path)
    assert first.stdout != b""
    second = run_hook(prompt_payload(cwd, transcript, "w2"), tmp_path)
    assert second.returncode == 0
    assert second.stdout == b""


def test_downward_crossing_resets_silently_then_refires(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    assert run_hook(prompt_payload(cwd, transcript, "w3"), tmp_path).stdout != b""
    write_transcript(transcript, 20_000)  # 10% -> compaction/clear happened
    down = run_hook(prompt_payload(cwd, transcript, "w3"), tmp_path)
    assert down.returncode == 0
    assert down.stdout == b""
    assert read_state(tmp_path, "w3")["watch"]["last_band"] == -1
    write_transcript(transcript, 120_000)  # up again -> re-fires
    text = parse_output(run_hook(prompt_payload(cwd, transcript, "w3"), tmp_path))
    assert "~120,000 tokens" in text


def test_jump_straight_to_highest_band(tmp_path):
    cwd, transcript = setup_session(tmp_path, 180_000)  # 90% -> band 2
    text = parse_output(run_hook(prompt_payload(cwd, transcript, "w4"), tmp_path))
    assert "near or past the end of your working budget" in text
    assert "~180,000 tokens" in text
    assert read_state(tmp_path, "w4")["watch"]["last_band"] == 2


def test_escalation_texts_and_lengths(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    markers = ("working budget", "degradation zone", "near or past the end of your working budget")
    for tokens, marker in zip((120_000, 150_000, 180_000), markers):
        write_transcript(transcript, tokens)
        text = parse_output(run_hook(prompt_payload(cwd, transcript, "w5"), tmp_path))
        assert marker in text
        assert len(text) < 1000


def test_throttle_accumulates_then_min_calls_triggers(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    env = {"DRY_WATCH_MIN_CALLS": "3", "DRY_WATCH_MIN_SECONDS": "3600"}
    for expected in (1, 2):
        result = run_hook(tool_payload(cwd, transcript, "w6"), tmp_path, env)
        assert result.returncode == 0
        assert result.stdout == b""
        assert read_state(tmp_path, "w6")["watch"]["calls_since"] == expected
        assert read_state(tmp_path, "w6")["watch"]["top"] == []  # 51-char stdout: no hog
    result = run_hook(tool_payload(cwd, transcript, "w6"), tmp_path, env)
    text = parse_output(result)
    assert "~120,000 tokens" in text
    envelope = json.loads(result.stdout)
    assert envelope["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert read_state(tmp_path, "w6")["watch"]["calls_since"] == 0


def test_time_expiry_triggers_check(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    env = {"DRY_WATCH_MIN_CALLS": "1000", "DRY_WATCH_MIN_SECONDS": "0"}
    text = parse_output(run_hook(tool_payload(cwd, transcript, "w7"), tmp_path, env))
    assert "~120,000 tokens" in text
    again = run_hook(tool_payload(cwd, transcript, "w7"), tmp_path, env)
    assert again.stdout == b""  # same band: no re-fire even when unthrottled


def test_clock_jump_treated_as_expired(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    state_dir = tmp_path / "claude-dry" / "w8"
    state_dir.mkdir(parents=True)
    state = {"watch": {"last_check_wall": time.time() + 10_000, "calls_since": 0,
                       "last_band": -1, "top": []}}
    (state_dir / "state.json").write_text(json.dumps(state))
    env = {"DRY_WATCH_MIN_CALLS": "1000", "DRY_WATCH_MIN_SECONDS": "3600"}
    text = parse_output(run_hook(tool_payload(cwd, transcript, "w8"), tmp_path, env))
    assert "~120,000 tokens" in text


def test_user_prompt_bypasses_throttle(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    env = {"DRY_WATCH_MIN_CALLS": "1000", "DRY_WATCH_MIN_SECONDS": "3600"}
    throttled = run_hook(tool_payload(cwd, transcript, "w9"), tmp_path, env)
    assert throttled.stdout == b""
    text = parse_output(run_hook(prompt_payload(cwd, transcript, "w9"), tmp_path, env))
    assert "~120,000 tokens" in text


def test_hog_tracking_appears_in_band0_message(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    env = {"DRY_WATCH_MIN_CALLS": "1000", "DRY_WATCH_MIN_SECONDS": "3600"}
    for fixture in ("00-Bash.json", "02-Read.json"):  # 36,475 and 71,882 chars
        result = run_hook(tool_payload(cwd, transcript, "w10", fixture), tmp_path, env)
        assert result.stdout == b""
    top = read_state(tmp_path, "w10")["watch"]["top"]
    assert [e[1] for e in top] == ["Read", "Bash"]  # size-descending
    assert top[0][0] > top[1][0] > 2000
    text = parse_output(run_hook(prompt_payload(cwd, transcript, "w10"), tmp_path, env))
    assert "Largest tool results so far: Read 72k, Bash 36k chars." in text


def test_top_hogs_capped_at_five(tmp_path):
    cwd, transcript = setup_session(tmp_path, 20_000)
    env = {"DRY_WATCH_MIN_CALLS": "1000", "DRY_WATCH_MIN_SECONDS": "3600"}
    payload = tool_payload(cwd, transcript, "w11")
    for i in range(7):
        payload["tool_response"] = {"stdout": "x" * (3_000 + i), "stderr": "",
                                    "interrupted": False, "isImage": False}
        run_hook(payload, tmp_path, env)
    top = read_state(tmp_path, "w11")["watch"]["top"]
    assert len(top) == 5
    assert top == sorted(top, key=lambda e: -e[0])


def test_disable_flags_suppress_everything(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    for flag in ("DRY_DISABLE_WATCH", "DRY_DISABLE"):
        result = run_hook(prompt_payload(cwd, transcript, "w12"), tmp_path, {flag: "1"})
        assert result.returncode == 0
        assert result.stdout == b""
    # disable is checked first: no state was ever written
    assert not (tmp_path / "claude-dry" / "w12" / "state.json").exists()


def test_malformed_stdin_exits_zero_silently(tmp_path):
    for garbage in (b"not json at all {{", b"", b"[1, 2, 3]"):
        result = run_hook(garbage, tmp_path)
        assert result.returncode == 0
        assert result.stdout == b""
        assert result.stderr == b""


def test_missing_transcript_exits_zero(tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    payload = prompt_payload(cwd, tmp_path / "nope.jsonl", "w13")
    result = run_hook(payload, tmp_path)
    assert result.returncode == 0
    assert result.stdout == b""
    watch = read_state(tmp_path, "w13")["watch"]  # state still saved (reset)
    assert watch["calls_since"] == 0
    assert watch["last_band"] == -1


def test_probe_fixture_exact_token_count():
    info = _common.transcript_context_tokens(str(PROBE_TRANSCRIPT))
    assert info is not None
    assert info["tokens"] == 64_677
    assert info["model"] == "claude-fable-5"


def test_probe_fixture_crosses_low_band(tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    payload = prompt_payload(cwd, PROBE_TRANSCRIPT, "w14")
    env = {"DRY_BANDS": "[0.2]", "DRY_REFERENCE_WINDOW": "200000"}
    text = parse_output(run_hook(payload, tmp_path, env))
    assert "~64,677 tokens" in text
    assert "~32% of your 200,000-token working budget" in text
    assert len(text) < 1000


def test_probe_fixture_silent_with_defaults(tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()
    result = run_hook(prompt_payload(cwd, PROBE_TRANSCRIPT, "w15"), tmp_path)
    assert result.returncode == 0
    assert result.stdout == b""  # 32% < 50% default first band


def test_state_file_permissions(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    run_hook(prompt_payload(cwd, transcript, "w16"), tmp_path)
    state_path = tmp_path / "claude-dry" / "w16" / "state.json"
    assert state_path.is_file()
    assert state_path.stat().st_mode & 0o777 == 0o600
    assert state_path.parent.stat().st_mode & 0o777 == 0o700


# ------------------------------------------------------------ gate-mode wording

def test_gated_run_advisories_never_route_compaction_via_user(tmp_path):
    cwd, transcript = setup_session(tmp_path, 150_000)  # 75% -> band 1
    gate = {"DRY_GATE": "1"}
    text = parse_output(run_hook(prompt_payload(cwd, transcript, "g1"), tmp_path, gate))
    assert "Gated run" in text
    assert "do not propose /compact" in text
    assert "recommend" not in text and "compaction point" not in text
    write_transcript(transcript, 180_000)  # 90% -> band 2
    text = parse_output(run_hook(prompt_payload(cwd, transcript, "g1"), tmp_path, gate))
    assert "minimum at which a compaction pays off" in text
    assert "Nothing to tell the user" in text
    assert "propose" not in text and "recommend" not in text
    assert len(text) < 1000


def test_gated_run_first_band_keeps_hygiene_menu(tmp_path):
    cwd, transcript = setup_session(tmp_path, 120_000)
    text = parse_output(run_hook(prompt_payload(cwd, transcript, "g2"), tmp_path, {"DRY_GATE": "1"}))
    assert "working budget" in text and "subagents" in text
    assert "nothing to raise with the user" in text


def test_interactive_run_advisories_keep_user_suggestion(tmp_path):
    cwd, transcript = setup_session(tmp_path, 150_000)
    text = parse_output(run_hook(prompt_payload(cwd, transcript, "i1"), tmp_path))
    assert "Gated run" not in text
    assert "/compact" in text and "one line" in text
    assert "compaction point" not in text
