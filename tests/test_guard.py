"""Subprocess tests for scripts/dry_guard.py (AVOID leg, DESIGN.md §4.3).

Every test runs the hook exactly as Claude Code does: a fresh python3
process fed JSON on stdin, output on stdout only. Fixture payloads are real
captured PostToolUse payloads (tests/fixtures/payloads/); their cwd fields
are rewritten to the test tmp dir before feeding.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "dry_guard.py"
PAYLOADS = REPO / "tests" / "fixtures" / "payloads"

HEAD, TAIL = 6_000, 4_000  # defaults from _common.DEFAULTS


def run_guard(payload, tmp_path: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode("utf-8")
    full_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),  # nothing may ever touch the real ~/.claude
        "TMPDIR": str(tmp_path),  # debug.log lands under the test tmp
    }
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "scripts/dry_guard.py"],
        input=payload,
        capture_output=True,
        cwd=str(REPO),
        env=full_env,
    )


def load_payload(name: str, tmp_path: Path) -> dict:
    data = json.loads((PAYLOADS / name).read_text(encoding="utf-8"))
    data["cwd"] = str(tmp_path)
    data["transcript_path"] = str(tmp_path / "transcript.jsonl")
    return data


def updated_output(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0
    assert proc.stderr == b""
    out = json.loads(proc.stdout.decode("utf-8"))
    assert set(out) == {"hookSpecificOutput"}
    hso = out["hookSpecificOutput"]
    assert set(hso) == {"hookEventName", "updatedToolOutput"}
    assert hso["hookEventName"] == "PostToolUse"
    return hso["updatedToolOutput"]


def assert_silent(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert proc.stderr == b""


def archive_files(tmp_path: Path) -> list[Path]:
    root = tmp_path / ".claude" / "dry" / "archive"
    return sorted(root.rglob("*.txt")) if root.exists() else []


def expected_marker(total: int, pointer: str, head: int = HEAD, tail: int = TAIL) -> str:
    return (
        f"\n\n[dry diverted {total - head - tail:,} of {total:,} chars. {pointer}. "
        f"Middle omitted — retrieve what you need rather than re-running the command.]\n\n"
    )


def assert_stub(stub: str, original: str, pointer: str) -> None:
    """The stub is exactly head + contract marker + tail of the original."""
    assert stub == original[:HEAD] + expected_marker(len(original), pointer) + original[-TAIL:]


def varied_text(n: int) -> str:
    """n chars with no repeating window, so head/tail slices are meaningful."""
    return ("".join(f"{i:07d}\n" for i in range(n // 8 + 1)))[:n]


def bash_payload(tmp_path: Path, stdout: str, stderr: str = "", interrupted: bool = False,
                 is_image: bool = False) -> dict:
    data = load_payload("04-Bash.json", tmp_path)
    data["tool_response"].update(
        stdout=stdout, stderr=stderr, interrupted=interrupted, isImage=is_image
    )
    return data


# ---------------------------------------------------------------- Read


def test_read_fixture_diverted(tmp_path):
    data = load_payload("02-Read.json", tmp_path)
    orig = data["tool_response"]
    content = orig["file"]["content"]
    assert len(content) == 70_583  # fixture sanity

    updated = updated_output(run_guard(data, tmp_path))

    # Shape fidelity: every key preserved, in order, byte-identical siblings.
    assert list(updated) == list(orig)
    assert updated["type"] == "text"
    assert list(updated["file"]) == list(orig["file"])
    for key in ("filePath", "numLines", "startLine", "totalLines", "truncatedByTokenCap"):
        assert updated["file"][key] == orig["file"][key]

    pointer = (
        f"The source file is the archive: re-Read {orig['file']['filePath']} "
        f"with offset/limit for specific ranges"
        f", note: this view was itself already truncated by a token cap"
    )
    assert_stub(updated["file"]["content"], content, pointer)
    marker = updated["file"]["content"][HEAD:-TAIL]
    assert "60,583 of 70,583 chars" in marker
    assert "/tmp/dry-probe/big.txt" in marker
    assert "token cap" in marker

    # The source file is the archive: nothing written to disk.
    assert archive_files(tmp_path) == []


def test_read_without_token_cap_note(tmp_path):
    data = load_payload("02-Read.json", tmp_path)
    data["tool_response"]["file"]["truncatedByTokenCap"] = False
    updated = updated_output(run_guard(data, tmp_path))
    marker = updated["file"]["content"][HEAD:-TAIL]
    assert "token cap" not in marker
    assert marker.endswith("re-running the command.]\n\n")


# ---------------------------------------------------------------- Bash


def test_bash_persisted_pointer_no_archive(tmp_path):
    data = load_payload("00-Bash.json", tmp_path)
    orig = data["tool_response"]
    assert len(orig["stdout"]) == 30_000  # fixture sanity

    updated = updated_output(run_guard(data, tmp_path))

    assert list(updated) == list(orig)
    pointer = (
        f"Full output: {orig['persistedOutputPath']} "
        f"({orig['persistedOutputSize']:,} bytes; Read or Grep it)"
    )
    assert "(168,894 bytes; Read or Grep it)" in pointer  # fixture sanity
    assert_stub(updated["stdout"], orig["stdout"], pointer)
    for key in orig:
        if key != "stdout":
            assert updated[key] == orig[key]

    # Native archive already exists — we must not write our own copy.
    assert not (tmp_path / ".claude" / "dry" / "archive").exists()


def test_bash_archived(tmp_path):
    data = load_payload("03-Bash.json", tmp_path)
    orig = data["tool_response"]
    assert len(orig["stdout"]) == 16_563  # > default 16_000 threshold
    assert "persistedOutputPath" not in orig

    updated = updated_output(run_guard(data, tmp_path))

    files = archive_files(tmp_path)
    assert len(files) == 1
    path = files[0]
    assert path.parent.name == "a8ba0f4e"  # sid8 of the fixture session_id
    assert path.name.endswith("-bash.txt")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    text = path.read_text(encoding="utf-8")
    header, body = text.split("\n\n", 1)
    lines = header.split("\n")
    assert len(lines) == 3
    assert lines[0] == "tool: Bash"
    assert lines[1].startswith("time: ") and lines[1].endswith("Z")
    assert lines[2] == "command: grep -rn 'alpha' /tmp/dry-probe/data"
    assert body == orig["stdout"]  # full original stdout, untouched

    assert_stub(updated["stdout"], orig["stdout"], f"Full output archived: {path}")
    for key in orig:
        if key != "stdout":
            assert updated[key] == orig[key]


@pytest.mark.parametrize("name", ["04-Bash.json", "05-WebFetch.json"])
def test_small_outputs_untouched(tmp_path, name):
    assert_silent(run_guard(load_payload(name, tmp_path), tmp_path))
    assert archive_files(tmp_path) == []


def test_bash_errorish_raises_threshold(tmp_path):
    # 20k stdout with non-empty stderr: 20_000 < 16_000 * 3 -> not diverted.
    data = bash_payload(tmp_path, varied_text(20_000), stderr="grep: fatal: something broke")
    assert_silent(run_guard(data, tmp_path))
    assert archive_files(tmp_path) == []


def test_bash_interrupted_raises_threshold(tmp_path):
    data = bash_payload(tmp_path, varied_text(20_000), interrupted=True)
    assert_silent(run_guard(data, tmp_path))


def test_bash_whitespace_stderr_is_not_errorish(tmp_path):
    data = bash_payload(tmp_path, varied_text(20_000), stderr=" \n\t ")
    updated = updated_output(run_guard(data, tmp_path))
    assert updated["stderr"] == " \n\t "  # untouched even so


def test_bash_errorish_diverts_past_tripled_threshold_stderr_untouched(tmp_path):
    stdout = varied_text(60_000)  # > 48_000
    stderr = "E: " + varied_text(5_000)  # big stderr must survive in full
    data = bash_payload(tmp_path, stdout, stderr=stderr)
    updated = updated_output(run_guard(data, tmp_path))
    assert updated["stderr"] == stderr
    path = archive_files(tmp_path)[0]
    assert_stub(updated["stdout"], stdout, f"Full output archived: {path}")
    assert path.read_text(encoding="utf-8").split("\n\n", 1)[1] == stdout


def test_bash_is_image_skipped(tmp_path):
    data = bash_payload(tmp_path, varied_text(30_000), is_image=True)
    assert_silent(run_guard(data, tmp_path))
    assert archive_files(tmp_path) == []


# ---------------------------------------------------------------- WebFetch


def test_webfetch_huge_archived_and_rewritten(tmp_path):
    data = load_payload("05-WebFetch.json", tmp_path)
    orig = data["tool_response"]
    result = varied_text(24_000)
    orig["result"] = result

    updated = updated_output(run_guard(data, tmp_path))

    files = archive_files(tmp_path)
    assert len(files) == 1
    path = files[0]
    assert path.name.endswith("-webfetch.txt")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    header, body = path.read_text(encoding="utf-8").split("\n\n", 1)
    lines = header.split("\n")
    assert lines[0] == "tool: WebFetch"
    assert lines[2] == "url: https://example.com"
    assert body == result

    assert list(updated) == list(orig)
    assert_stub(updated["result"], result, f"Full output archived: {path}")
    for key in ("bytes", "code", "codeText", "durationMs", "url"):
        assert updated[key] == orig[key]


# ---------------------------------------------------------------- protections


def test_idempotent_stub_never_rediverted(tmp_path):
    data = load_payload("03-Bash.json", tmp_path)
    updated = updated_output(run_guard(data, tmp_path))
    assert len(archive_files(tmp_path)) == 1

    # Feed the stubbed output back with a threshold the stub itself exceeds:
    # without the marker check this WOULD re-divert (stub > 10_000 chars).
    data2 = load_payload("03-Bash.json", tmp_path)
    data2["tool_response"] = updated
    assert len(updated["stdout"]) > 1_000
    proc = run_guard(data2, tmp_path, env={"DRY_GUARD_THRESHOLD_CHARS": "1000"})
    assert_silent(proc)
    assert len(archive_files(tmp_path)) == 1  # no second archive


def test_unknown_tool_shapes_ignored(tmp_path):
    # Grep is in the hook matcher but its shape was never captured -> silent,
    # even with a plausible-looking huge response.
    grep_guess = {
        "session_id": "s",
        "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse",
        "tool_name": "Grep",
        "tool_input": {"pattern": "alpha", "output_mode": "content"},
        "tool_response": {"mode": "content", "content": varied_text(30_000), "numLines": 3750},
    }
    assert_silent(run_guard(grep_guess, tmp_path))
    # A real captured payload for a tool we do not rewrite -> silent too.
    assert_silent(run_guard(load_payload("01-ToolSearch.json", tmp_path), tmp_path))
    assert archive_files(tmp_path) == []


def test_bash_shape_mismatch_ignored(tmp_path):
    data = load_payload("03-Bash.json", tmp_path)
    del data["tool_response"]["stderr"]  # not the documented Bash shape
    assert_silent(run_guard(data, tmp_path))

    data = load_payload("03-Bash.json", tmp_path)
    data["tool_response"]["interrupted"] = "no"  # not bool-ish
    assert_silent(run_guard(data, tmp_path))


@pytest.mark.parametrize(
    "raw",
    [b"", b"not json {{{", b"[1, 2, 3]", b'"just a string"', b'{"tool_name": "Bash"}'],
)
def test_malformed_stdin_silent_exit_zero(tmp_path, raw):
    assert_silent(run_guard(raw, tmp_path))


def test_stub_never_larger_than_original(tmp_path):
    # 5_000 chars beats a 1_000 threshold but is under head+tail (10_000):
    # a stub would be BIGGER than the original, so the guard leaves it alone.
    data = bash_payload(tmp_path, varied_text(5_000))
    proc = run_guard(data, tmp_path, env={"DRY_GUARD_THRESHOLD_CHARS": "1000"})
    assert_silent(proc)


# ---------------------------------------------------------------- config


def test_env_threshold_override(tmp_path):
    # 03-Bash (16_563 chars) with DRY_GUARD_THRESHOLD_CHARS=1000 still triggers
    # the archive path and exact stub.
    data = load_payload("03-Bash.json", tmp_path)
    updated = updated_output(
        run_guard(data, tmp_path, env={"DRY_GUARD_THRESHOLD_CHARS": "1000"})
    )
    path = archive_files(tmp_path)[0]
    assert_stub(updated["stdout"], data["tool_response"]["stdout"], f"Full output archived: {path}")


def test_config_file_threshold(tmp_path):
    cfg_dir = tmp_path / ".claude" / "dry"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(json.dumps({"guard_threshold_chars": 1_000}))
    data = bash_payload(tmp_path, varied_text(12_000))  # under default, over 1_000
    updated = updated_output(run_guard(data, tmp_path))
    assert "[dry diverted" in updated["stdout"]


@pytest.mark.parametrize("env", [{"DRY_DISABLE": "1"}, {"DRY_DISABLE_GUARD": "true"}])
def test_disable_flags(tmp_path, env):
    assert_silent(run_guard(load_payload("02-Read.json", tmp_path), tmp_path, env=env))
