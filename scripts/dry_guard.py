"""AVOID leg (DESIGN.md §4.3): reversibly divert oversized tool results.

PostToolUse hook, matcher Bash|Read|Grep|Glob|WebFetch|WebSearch. When a
positively-identified tool_response carries one pathologically large field,
the full content stays retrievable on disk (Claude Code's own persisted
copy, the source file itself, or an archive we write under
<cwd>/.claude/dry/archive/) and a head+tail stub with a pointer replaces
what enters context, via hookSpecificOutput.updatedToolOutput.

Fail-open twice over: Claude Code ignores a rewrite whose shape does not
match the tool's schema, and this script silently exits 0 on any shape it
does not positively recognize (and on any error, via _common.fail_open).
Only plugin-generated text (counts, fixed phrases, paths) is added to what
enters context; the head/tail slices are original content returned to its
own slot. Tool content never goes anywhere except that slot and the
archive file on disk.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _common import (
        emit,
        fail_open,
        load_config,
        now_stamp,
        project_dry_dir,
        read_stdin_json,
    )
except Exception:  # import failure must never break the session
    sys.exit(0)

# Idempotence sentinel: a field already carrying this is never diverted again.
MARKER_TAG = "[dry diverted"


def _boolish(v) -> bool:
    return isinstance(v, (bool, int)) and v in (0, 1)


def _sid8(session_id) -> str:
    if session_id is None:
        return "unknown"
    sid = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return sid[:8] or "unknown"


def _header_line(text) -> str:
    return " ".join(str(text).split())[:200]


def _limits(cfg: dict) -> tuple[int, int, int]:
    return (
        cfg["guard_threshold_chars"],
        max(0, cfg["guard_head_chars"]),
        max(0, cfg["guard_tail_chars"]),
    )


def _stub(text: str, head: int, tail: int, pointer: str) -> str:
    """Head + self-documenting marker + tail. Marker text is plugin-generated
    only: counts, a fixed phrase, and a pointer (path or fixed instruction)."""
    total = len(text)
    marker = (
        f"\n\n[dry diverted {total - head - tail:,} of {total:,} chars. {pointer}. "
        f"Middle omitted — retrieve what you need rather than re-running the command.]\n\n"
    )
    return text[:head] + marker + (text[total - tail:] if tail > 0 else "")


def _archive(cwd: str, session_id, tool: str, input_line: str, body: str) -> Path:
    """Write full content to <cwd>/.claude/dry/archive/<sid8>/<stamp>-<tool>.txt.

    3-line header (tool, UTC timestamp, input summary; each flattened and
    truncated to 200 chars), blank line, then the untouched body. Files land
    0600 (umask + explicit mode); O_EXCL sidesteps same-second collisions.
    """
    stamp = now_stamp()
    root = project_dry_dir(cwd, create=True) / "archive" / _sid8(session_id)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    header = "\n".join(
        (
            _header_line(f"tool: {tool}"),
            _header_line(f"time: {stamp}"),
            _header_line(input_line),
        )
    )
    suffix = tool.lower()
    for attempt in range(1000):
        name = f"{stamp}-{suffix}.txt" if attempt == 0 else f"{stamp}-{suffix}-{attempt}.txt"
        path = root / name
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(header + "\n\n" + body)
        return path
    raise RuntimeError("dry_guard: could not allocate an archive filename")


def _divert_bash(data: dict, resp: dict, cfg: dict) -> dict | None:
    stdout, stderr = resp.get("stdout"), resp.get("stderr")
    if not (isinstance(stdout, str) and isinstance(stderr, str)):
        return None
    if not (_boolish(resp.get("interrupted")) and _boolish(resp.get("isImage"))):
        return None
    if resp["isImage"]:
        return None  # never divert image results
    if MARKER_TAG in stdout:
        return None  # already stubbed
    threshold, head, tail = _limits(cfg)
    if stderr.strip() or resp["interrupted"]:
        # Error-ish: raise the bar, and never touch stderr — errors stay visible.
        threshold *= cfg["guard_error_multiplier"]
    if len(stdout) <= threshold or len(stdout) <= head + tail:
        return None
    persisted = resp.get("persistedOutputPath")
    if isinstance(persisted, str) and persisted:
        # Native Bash truncation already archived the full output — point at
        # that copy rather than writing our own.
        size = resp.get("persistedOutputSize")
        if isinstance(size, int) and not isinstance(size, bool):
            pointer = f"Full output: {persisted} ({size:,} bytes; Read or Grep it)"
        else:
            pointer = f"Full output: {persisted} (Read or Grep it)"
    else:
        tool_input = data.get("tool_input")
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        path = _archive(data["cwd"], data.get("session_id"), "Bash", f"command: {command}", stdout)
        pointer = f"Full output archived: {path}"
    updated = dict(resp)
    updated["stdout"] = _stub(stdout, head, tail, pointer)
    return updated


def _divert_read(data: dict, resp: dict, cfg: dict) -> dict | None:
    if resp.get("type") != "text" or not isinstance(resp.get("file"), dict):
        return None
    file = resp["file"]
    content, file_path = file.get("content"), file.get("filePath")
    if not (isinstance(content, str) and isinstance(file_path, str)):
        return None
    if MARKER_TAG in content:
        return None
    threshold, head, tail = _limits(cfg)
    if len(content) <= threshold or len(content) <= head + tail:
        return None
    # No archive write: the source file IS the archive.
    pointer = (
        f"The source file is the archive: re-Read {file_path} "
        f"with offset/limit for specific ranges"
    )
    if file.get("truncatedByTokenCap"):
        pointer += ", note: this view was itself already truncated by a token cap"
    updated_file = dict(file)
    updated_file["content"] = _stub(content, head, tail, pointer)
    updated = dict(resp)
    updated["file"] = updated_file
    return updated


def _divert_webfetch(data: dict, resp: dict, cfg: dict) -> dict | None:
    result, url = resp.get("result"), resp.get("url")
    if not (isinstance(result, str) and isinstance(url, str)):
        return None
    if MARKER_TAG in result:
        return None
    threshold, head, tail = _limits(cfg)
    if len(result) <= threshold or len(result) <= head + tail:
        return None
    path = _archive(data["cwd"], data.get("session_id"), "WebFetch", f"url: {url}", result)
    updated = dict(resp)
    updated["result"] = _stub(result, head, tail, f"Full output archived: {path}")
    return updated


# Positively-identified tool_response shapes, built against real captured
# payloads in tests/fixtures/payloads/. Any tool_name absent here is left
# alone (silent exit). To support another tool later: capture a real payload,
# write a _divert_<tool> that positively checks the shape, add one line here.
SHAPES = {
    "Bash": _divert_bash,  # {stdout, stderr, interrupted, isImage, [persistedOutputPath, ...]}
    "Read": _divert_read,  # {type: "text", file: {filePath, content, numLines, ...}}
    "WebFetch": _divert_webfetch,  # {bytes, code, codeText, result, durationMs, url}
    # "Grep":      shape not yet captured (deferred tool on this box) -> skipped
    # "Glob":      shape not yet captured -> skipped
    # "WebSearch": shape not yet captured -> skipped
}


def main() -> None:
    data = read_stdin_json()
    cwd = data["cwd"]
    cfg = load_config(cwd)
    if cfg["disable"] or cfg["disable_guard"]:
        return
    if not (isinstance(cwd, str) and os.path.isabs(cwd)):
        return  # anomaly: never archive to a relative/odd location
    handler = SHAPES.get(data.get("tool_name"))
    resp = data.get("tool_response")
    if handler is None or not isinstance(resp, dict):
        return
    updated = handler(data, resp, cfg)
    if updated is None:
        return
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": updated,
            }
        }
    )


if __name__ == "__main__":
    fail_open(main)
