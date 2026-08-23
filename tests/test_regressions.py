"""Regression tests for the adversarial-review fixes (2026-08-23).

Each test pins one confirmed finding:
  1. sub-1MiB transcript with the usage record on line 1 (first-line drop)
  2. compact_boundary handling (stale-high window -> postTokens or unknown)
  3. bool values rejected for int/float config keys (bool is a subclass of int)
  4. dry_status caps the ledger goal line (was uncapped)
  5. rehydrate bounds its ledger read (was unbounded read_text)
  6. import failure exits 0 silently (fail-open even before _common loads)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _common  # noqa: E402


def usage_record(tokens: int, extra: dict | None = None) -> str:
    rec = {
        "type": "assistant",
        "message": {
            "model": "claude-fable-5",
            "usage": {
                "input_tokens": tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    }
    rec.update(extra or {})
    return json.dumps(rec)


def run_script(script: str, payload: dict, tmp_path: Path, env_extra: dict | None = None):
    env = dict(os.environ)
    env["TMPDIR"] = str(tmp_path / "tmp")
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env=env,
        timeout=30,
    )


def test_usage_record_on_line_one_of_small_transcript(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(usage_record(51_100) + "\n")
    info = _common.transcript_context_tokens(str(t))
    assert info is not None and info["tokens"] == 51_100


def test_compact_boundary_without_posttokens_returns_unknown(tmp_path):
    t = tmp_path / "t.jsonl"
    boundary = json.dumps(
        {"type": "system", "subtype": "compact_boundary",
         "compactMetadata": {"trigger": "auto", "preTokens": 788_383}}
    )
    t.write_text(usage_record(788_383) + "\n" + boundary + "\n")
    assert _common.transcript_context_tokens(str(t)) is None


def test_compact_boundary_with_posttokens_uses_them(tmp_path):
    t = tmp_path / "t.jsonl"
    boundary = json.dumps(
        {"type": "system", "subtype": "compact_boundary",
         "compactMetadata": {"trigger": "auto", "preTokens": 788_383, "postTokens": 21_289}}
    )
    t.write_text(usage_record(788_383) + "\n" + boundary + "\n")
    info = _common.transcript_context_tokens(str(t))
    assert info is not None and info["tokens"] == 21_289


def test_usage_after_boundary_wins(tmp_path):
    t = tmp_path / "t.jsonl"
    boundary = json.dumps({"type": "system", "subtype": "compact_boundary"})
    t.write_text(usage_record(700_000) + "\n" + boundary + "\n" + usage_record(30_000) + "\n")
    info = _common.transcript_context_tokens(str(t))
    assert info is not None and info["tokens"] == 30_000


def test_bool_config_value_rejected_for_int_key(tmp_path, monkeypatch):
    dry = tmp_path / ".claude" / "dry"
    dry.mkdir(parents=True)
    (dry / "config.json").write_text(json.dumps({"reference_window": True, "disable": True}))
    for key in _common.DEFAULTS:
        monkeypatch.delenv("DRY_" + key.upper(), raising=False)
    cfg = _common.load_config(str(tmp_path))
    assert cfg["reference_window"] == _common.DEFAULTS["reference_window"]
    assert cfg["disable"] is True  # bool keys still accept bools


def test_status_goal_line_capped(tmp_path):
    dry = tmp_path / ".claude" / "dry"
    dry.mkdir(parents=True)
    (dry / "ledger.md").write_text("# Ledger\n\n## Goal\n" + "g" * 5000 + "\n")
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "dry_status.py"), "--cwd", str(tmp_path)],
        capture_output=True, timeout=30,
        env={**os.environ, "TMPDIR": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path / "nohome")},
    )
    assert out.returncode == 0
    ledger_lines = [l for l in out.stdout.decode().splitlines() if "goal:" in l]
    assert ledger_lines and len(ledger_lines[0]) < 300


def test_rehydrate_bounded_on_huge_ledger(tmp_path):
    dry = tmp_path / ".claude" / "dry"
    dry.mkdir(parents=True)
    (dry / "ledger.md").write_text("## Goal\nbig\n" + "x" * (10 * 1024 * 1024))
    out = run_script(
        "dry_rehydrate.py",
        {"hook_event_name": "SessionStart", "source": "compact",
         "session_id": "s1", "cwd": str(tmp_path), "transcript_path": "/nonexistent"},
        tmp_path,
    )
    assert out.returncode == 0
    body = json.loads(out.stdout.decode())
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert len(ctx) < 6_500 and "[dry: ledger truncated" in ctx


def test_import_failure_exits_zero_silently(tmp_path):
    # A hook script stranded without _common (broken install) must stay silent.
    lonely = tmp_path / "dry_watch.py"
    shutil.copy(SCRIPTS / "dry_watch.py", lonely)
    out = subprocess.run(
        [sys.executable, str(lonely)],
        input=b"{}", capture_output=True, timeout=30, cwd=str(tmp_path),
        env={**os.environ, "TMPDIR": str(tmp_path)},
    )
    assert out.returncode == 0
    assert out.stdout == b""
