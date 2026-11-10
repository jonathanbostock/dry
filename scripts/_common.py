"""Shared helpers for dry hooks. Python >= 3.10, stdlib only.

Contract for every hook in this plugin:
  - NEVER break the session. Any internal error -> exit 0, empty stdout.
  - NEVER block, deny, or exit 2.
  - Anything injected into context is plugin-generated text only (numbers,
    fixed phrases, paths under dry's own directories) — never tool content.
  - Everything written to disk: dirs 0700, files 0600 (enforced via umask).

Diagnostics from swallowed errors go to <tmp>/claude-dry/debug.log; set
DRY_DEBUG=1 to also mirror them to stderr (visible in `claude --debug`).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

os.umask(0o077)

MAX_STDIN_BYTES = 64 * 1024 * 1024  # PostToolUse payloads can be large
TRANSCRIPT_TAIL_BYTES = 1024 * 1024

DEFAULTS = {
    "reference_window": 200_000,
    "bands": [0.50, 0.70, 0.85],
    "watch_min_calls": 25,
    "watch_min_seconds": 60,
    "guard_threshold_chars": 16_000,
    "guard_head_chars": 6_000,
    "guard_tail_chars": 4_000,
    "guard_error_multiplier": 3,
    "snapshots_keep": 10,
    "snapshot_min_interval_seconds": 60,
    "ledger_pointer_max_age_days": 7,
    "rehydrate_ledger_chars": 6_000,
    "rehydrate_newest_item_chars": 2_000,
    "rehydrate_item_chars": 500,
    "rehydrate_detail_chars": 5_000,
    "rehydrate_stub_chars": 110,
    "gate_enabled": False,
    "gate_flag_max_age_minutes": 60,
    "gate_failsafe_fraction": 0.9,
    "gate_remind_minutes": 30,
    "gate_max_pending_minutes": 90,
    "gate_snapshot_min_interval_seconds": 600,
    "gate_max_overrun_tokens": 20_000,
    "gate_spiral_fraction": 0.75,
    "window_tokens": 0,
    "snapshot_max_bytes": 64 * 1024 * 1024,
    "snapshots_max_total_mb": 2048,
    "handoff_compactions": 40,
    "handoff_transcript_mb": 250,
    "disable": False,
    "disable_watch": False,
    "disable_guard": False,
    "disable_checkpoint": False,
    "disable_rehydrate": False,
}


def debug_root() -> Path:
    return Path(tempfile.gettempdir()) / "claude-dry"


def log_debug(msg: str) -> None:
    try:
        root = debug_root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(root / "debug.log", "a", encoding="utf-8") as f:
            f.write(f"{stamp} {msg}\n")
        if os.environ.get("DRY_DEBUG"):
            print(msg, file=sys.stderr)
    except Exception:
        pass  # debugging must never raise


def fail_open(main) -> None:
    """Run main(); on any exception log and exit 0 with no output."""
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log_debug(f"[{getattr(main, '__module__', '?')}] swallowed:\n{traceback.format_exc()}")
    sys.exit(0)


def read_stdin_json() -> dict:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("hook stdin was not a JSON object")
    return data


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def state_dir(session_id: str) -> Path:
    sid = "".join(c for c in str(session_id) if c.isalnum() or c in "-_") or "unknown"
    d = debug_root() / sid
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def project_root(cwd: str) -> str:
    """The directory the session started in (CLAUDE_PROJECT_DIR, which Claude
    Code exports to hooks), else cwd. Hook `cwd` follows the agent's shell
    `cd`, so anchoring here keeps the ledger, snapshots and gate markers in
    one place even when the agent wanders into subdirectories or worktrees."""
    env = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if env and os.path.isabs(env) and os.path.isdir(env):
        return env
    return cwd


def project_dry_dir(cwd: str, create: bool = False) -> Path:
    d = Path(project_root(cwd)) / ".claude" / "dry"
    if create:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _coerce(default, raw: str):
    try:
        if isinstance(default, bool):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
        if isinstance(default, list):
            val = json.loads(raw)
            return val if isinstance(val, list) else default
        return raw
    except Exception:
        return default


def _type_ok(default, v) -> bool:
    """Config value type check; bool is not an acceptable int/float (bool ⊂ int)."""
    if isinstance(default, bool):
        return isinstance(v, bool)
    if isinstance(default, (int, float)):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, type(default))


def load_config(cwd: str) -> dict:
    cfg = dict(DEFAULTS)
    try:
        path = project_dry_dir(cwd) / "config.json"
        if path.is_file() and path.stat().st_size < 64 * 1024:
            user = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                for k, v in user.items():
                    if k in cfg and _type_ok(cfg[k], v):
                        cfg[k] = v
    except Exception:
        log_debug(f"config.json ignored:\n{traceback.format_exc()}")
    for key, default in DEFAULTS.items():
        raw = os.environ.get("DRY_" + key.upper())
        if raw is not None:
            cfg[key] = _coerce(default, raw)
    return cfg


def gate_enabled(cfg: dict) -> bool:
    """True when the self-compaction gate is on: config `gate_enabled`
    (or env DRY_GATE_ENABLED via load_config) or the DRY_GATE launch flag."""
    if cfg.get("gate_enabled"):
        return True
    return os.environ.get("DRY_GATE", "").strip().lower() in ("1", "true", "yes", "on")


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def load_state(session_id: str) -> dict:
    try:
        path = state_dir(session_id) / "state.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_state(session_id: str, state: dict) -> None:
    atomic_write(state_dir(session_id) / "state.json", json.dumps(state))


def monotonic() -> float:
    return time.monotonic()


def wall() -> float:
    return time.time()


def transcript_context_tokens(path: str) -> dict | None:
    """Best-effort context size from the transcript tail.

    Scans the last TRANSCRIPT_TAIL_BYTES for the most recent main-chain
    assistant record carrying message.usage, and returns
    {"tokens": int, "model": str|None}. Context occupancy at that point is
    the full prompt the assistant saw plus its reply:
    input + cache_read_input + cache_creation_input + output tokens.
    Returns None whenever anything is off — callers must treat that as
    "unknown", not zero.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            offset = max(0, size - TRANSCRIPT_TAIL_BYTES)
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return None
    lines = chunk.split(b"\n")
    if offset > 0 and len(lines) > 1:
        lines = lines[1:]  # first line is partial only after a mid-file seek
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            # Compaction rewrote the conversation: any usage record behind this
            # boundary is pre-compact and would read stale-high. Use the
            # boundary's own post-compaction count when present, else unknown.
            meta = rec.get("compactMetadata")
            post = meta.get("postTokens") if isinstance(meta, dict) else None
            if isinstance(post, (int, float)) and not isinstance(post, bool) and post > 0:
                return {"tokens": int(post), "model": None}
            return None
        if rec.get("type") != "assistant":
            continue
        if rec.get("isSidechain"):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict) or "input_tokens" not in usage:
            continue

        def n(key: str) -> int:
            v = usage.get(key)
            return v if isinstance(v, (int, float)) else 0

        tokens = int(
            n("input_tokens")
            + n("cache_read_input_tokens")
            + n("cache_creation_input_tokens")
            + n("output_tokens")
        )
        if tokens <= 0:
            continue
        model = msg.get("model") if isinstance(msg.get("model"), str) else None
        return {"tokens": tokens, "model": model}
    return None


COMPACT_TAIL_BYTES = 8 * 1024 * 1024
WINDOW_1M = 1_000_000
WINDOW_200K = 200_000


def real_window(model: str | None, cfg: dict | None = None) -> int:
    """The model's real context window, for failsafes.

    Default 200k: every current Claude model is 200k unless its id carries the
    1M marker ("[1m]"), and guessing too large is the dangerous direction (the
    gate deferred compaction until the API rejected the prompt when this
    assumed 1M for any non-haiku model). Config `window_tokens` > 0 overrides.
    """
    if cfg:
        w = cfg.get("window_tokens")
        if isinstance(w, int) and not isinstance(w, bool) and w > 0:
            return w
    if os.environ.get("CLAUDE_CODE_DISABLE_1M_CONTEXT") == "1":
        return WINDOW_200K
    m = (model or "").lower()
    if "[1m]" in m or m.endswith("-1m"):
        return WINDOW_1M
    return WINDOW_200K


def _usage_total(rec: dict) -> int | None:
    if rec.get("type") != "assistant" or rec.get("isSidechain"):
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict) or "input_tokens" not in usage:
        return None
    total = 0
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"):
        v = usage.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total += int(v)
    return total if total > 0 else None


def compaction_epochs(path: str, tail_bytes: int = COMPACT_TAIL_BYTES) -> list[dict]:
    """Compaction epochs visible in the transcript tail, oldest first.

    Each is {"pre": preTokens, "post": postTokens, "baseline": tokens of the
    first main-chain assistant call after the boundary, or None}. Only the
    tail is read: an epoch that has produced more than `tail_bytes` of
    transcript is not a spiral, which is all callers ask.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            offset = max(0, size - tail_bytes)
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return []
    lines = chunk.split(b"\n")
    if offset > 0 and len(lines) > 1:
        lines = lines[1:]
    epochs: list[dict] = []
    for raw in lines:
        raw = raw.strip()
        if not raw or b"usage" not in raw and b"compact_boundary" not in raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            meta = rec.get("compactMetadata")
            meta = meta if isinstance(meta, dict) else {}
            pre = meta.get("preTokens")
            post = meta.get("postTokens")
            epochs.append({
                "pre": int(pre) if isinstance(pre, (int, float)) and not isinstance(pre, bool) else None,
                "post": int(post) if isinstance(post, (int, float)) and not isinstance(post, bool) else None,
                "baseline": None,
            })
            continue
        if epochs and epochs[-1]["baseline"] is None:
            total = _usage_total(rec)
            if total:
                epochs[-1]["baseline"] = total
    return epochs


def spiral_epoch(epochs: list[dict], fraction: float) -> dict | None:
    """The newest epoch whose post-compaction baseline is already at least
    `fraction` of the size the harness compacts at — i.e. compaction bought
    almost nothing and will again. None when the newest measurable epoch is
    healthy or nothing is measurable."""
    if not isinstance(fraction, (int, float)) or fraction <= 0:
        return None
    for e in reversed(epochs):
        if e["baseline"] is not None and e["pre"]:
            return e if e["baseline"] >= fraction * e["pre"] else None
    return None


def cap_text(text: str, limit: int, marker: str = "\n[dry: truncated]") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(marker))] + marker
