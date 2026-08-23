#!/usr/bin/env bash
# Live smoke test for dry hooks — runs REAL headless Claude Code sessions in a
# sandbox project with hooks wired via project settings (no plugin install
# needed; hook mechanics are identical). Costs a few short API calls.
#
# Usage: bash tests/smoke_live.sh
# Requires: claude CLI authenticated, jq, python3. Exit 0 = all checks passed.
set -u

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$(mktemp -d /tmp/dry-smoke-XXXXXX)"
MODEL="claude-fable-5"   # pinned: unpinned -p defaults elsewhere and costs more
PASS=0; FAIL=0

check() { # check <name> <ok-bool>
  if [ "$2" = "true" ]; then echo "PASS: $1"; PASS=$((PASS+1));
  else echo "FAIL: $1"; FAIL=$((FAIL+1)); fi
}

echo "sandbox: $SANDBOX  plugin: $PLUGIN_ROOT"
mkdir -p "$SANDBOX/.claude"

# Reference window tiny so the watch bands trip immediately; inline env prefix
# keeps us independent of how session env propagates to hooks.
W="DRY_REFERENCE_WINDOW=3000 DRY_WATCH_MIN_SECONDS=0 DRY_WATCH_MIN_CALLS=1 python3 $PLUGIN_ROOT/scripts/dry_watch.py"
G="python3 $PLUGIN_ROOT/scripts/dry_guard.py"
R="python3 $PLUGIN_ROOT/scripts/dry_rehydrate.py"

cat > "$SANDBOX/.claude/settings.json" <<EOF
{
  "hooks": {
    "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "$W", "timeout": 10 } ] } ],
    "PostToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "$W", "timeout": 10 } ] },
      { "matcher": "Bash|Read|Grep|Glob|WebFetch|WebSearch", "hooks": [ { "type": "command", "command": "$G", "timeout": 30 } ] }
    ],
    "SessionStart": [
      { "matcher": "compact|clear|resume", "hooks": [ { "type": "command", "command": "$R", "timeout": 10 } ] }
    ]
  }
}
EOF

python3 - "$SANDBOX" <<'EOF'
import sys
open(sys.argv[1] + "/big.txt", "w").write(
    "\n".join(f"line {i}: smoke-test filler for the dry guard to divert" for i in range(3000)))
EOF

TRANSCRIPT_DIR="$HOME/.claude/projects/$(python3 -c "
import re, sys
print(re.sub(r'[^A-Za-z0-9-]', '-', '$SANDBOX'))")"

# ---- Session 1: guard should divert the big Read; ledger written for later --
mkdir -p "$SANDBOX/.claude/dry"
printf '# dry ledger\n\n## Goal\nSmoke-test the dry plugin end to end\n' > "$SANDBOX/.claude/dry/ledger.md"

OUT1="$(cd "$SANDBOX" && timeout 240 claude -p \
  "Read $SANDBOX/big.txt, then reply with exactly: OK" \
  --model "$MODEL" --output-format json 2>&1)"
SID="$(printf '%s\n' "$OUT1" | awk '/^\{/{l=$0} END{print l}' | jq -r '.session_id // empty' 2>/dev/null)"
check "session 1 ran and returned a session id" "$( [ -n "$SID" ] && echo true || echo false )"

T1="$TRANSCRIPT_DIR/$SID.jsonl"
check "guard stub present in transcript (dry diverted)" \
  "$( grep -q 'dry diverted' "$T1" 2>/dev/null && echo true || echo false )"
true # watch advisory asserted on T1-or-T2 below

# ---- Session 2: resume -> SessionStart(resume) pointer + UserPromptSubmit watch
OUT2="$(cd "$SANDBOX" && timeout 240 claude -p --resume "$SID" \
  "Reply with exactly: HI" --model "$MODEL" --output-format json 2>&1)"
SID2="$(printf '%s\n' "$OUT2" | awk '/^\{/{l=$0} END{print l}' | jq -r '.session_id // empty' 2>/dev/null)"
T2="$TRANSCRIPT_DIR/${SID2:-$SID}.jsonl"
check "session 2 (resume) ran" "$( [ -n "$SID2" ] && echo true || echo false )"
check "rehydrate pointer injected on resume (task ledger ... exists)" \
  "$( grep -q 'task ledger from' "$T2" 2>/dev/null && echo true || echo false )"
check "watch advisory injected in either session ([dry] Context check)" \
  "$( (grep -q 'Context check' "$T1" 2>/dev/null || grep -q 'Context check' "$T2" 2>/dev/null) && echo true || echo false )"

# ---- Hygiene: hooks never broke anything; state files are private ----------
check "state dir private (0700)" "$( [ -z "$(find /tmp/claude-dry -maxdepth 1 -type d -not -perm 0700 -not -path /tmp/claude-dry 2>/dev/null)" ] && echo true || echo false )"
DBG="/tmp/claude-dry/debug.log"
if [ -f "$DBG" ]; then echo "--- debug.log tail (swallowed errors, if any) ---"; tail -5 "$DBG"; fi

echo; echo "smoke: $PASS passed, $FAIL failed  (sandbox kept at $SANDBOX for inspection)"
[ "$FAIL" -eq 0 ]
