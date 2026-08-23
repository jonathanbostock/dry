#!/usr/bin/env bash
# Live smoke for the COMPACTION path: forces a real auto-compact at a 100k
# window (the --autocompact minimum) and asserts every dry compaction hook
# fired with the field names we coded against. Costs ~130k input tokens.
#
#   PreCompact(auto)  -> snapshots/<stamp>-<sid8>-auto.jsonl.gz + ledger ⚠ line
#   PostCompact(auto) -> snapshots/<stamp>-<sid8>-summary-auto.md
#   SessionStart(compact) -> "[dry] Context was just compacted" injection
#
# Usage: bash tests/smoke_compact.sh   (exit 0 = all checks passed)
set -u

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$(mktemp -d /tmp/dry-smokec-XXXXXX)"
MODEL="claude-fable-5"
PASS=0; FAIL=0
check() { if [ "$2" = "true" ]; then echo "PASS: $1"; PASS=$((PASS+1)); else echo "FAIL: $1"; FAIL=$((FAIL+1)); fi; }

echo "sandbox: $SANDBOX"
mkdir -p "$SANDBOX/.claude/dry"
printf '# dry ledger\n\n## Goal\nCompaction-path smoke test\n' > "$SANDBOX/.claude/dry/ledger.md"

# Guard threshold raised sky-high: this smoke needs context to GROW so the
# 100k auto-compact window trips; diversion was live-tested separately.
W="python3 $PLUGIN_ROOT/scripts/dry_watch.py"
G="DRY_GUARD_THRESHOLD_CHARS=9999999 python3 $PLUGIN_ROOT/scripts/dry_guard.py"
C="python3 $PLUGIN_ROOT/scripts/dry_checkpoint.py"
R="python3 $PLUGIN_ROOT/scripts/dry_rehydrate.py"

cat > "$SANDBOX/.claude/settings.json" <<EOF
{
  "hooks": {
    "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "$W", "timeout": 10 } ] } ],
    "PostToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "$W", "timeout": 10 } ] },
      { "matcher": "Bash|Read|Grep|Glob|WebFetch|WebSearch", "hooks": [ { "type": "command", "command": "$G", "timeout": 30 } ] }
    ],
    "PreCompact": [ { "hooks": [ { "type": "command", "command": "$C", "timeout": 60 } ] } ],
    "PostCompact": [ { "hooks": [ { "type": "command", "command": "$C", "timeout": 30 } ] } ],
    "SessionStart": [ { "matcher": "compact|clear|resume", "hooks": [ { "type": "command", "command": "$R", "timeout": 10 } ] } ]
  }
}
EOF

python3 - "$SANDBOX" <<'EOF'
import sys
for i in range(1, 8):
    with open(f"{sys.argv[1]}/doc{i}.txt", "w") as f:
        f.write("\n".join(
            f"doc{i} line {j}: unique filler {i}-{j} for compaction smoke padding"
            for j in range(1600)))
EOF

PROMPT="Read these files one at a time, each with a single Read call, in order: $SANDBOX/doc1.txt, doc2.txt, doc3.txt, doc4.txt, doc5.txt, doc6.txt, doc7.txt (all in $SANDBOX). After each, state its number. Then reply with one line: ALLREAD."
OUT="$(cd "$SANDBOX" && timeout 540 claude -p "$PROMPT" \
  --model "$MODEL" --autocompact 100000 --output-format json 2>&1)"
LAST="$(printf '%s\n' "$OUT" | awk '/^\{/{l=$0} END{print l}')"
SID="$(printf '%s' "$LAST" | jq -r '.session_id // empty' 2>/dev/null)"
check "compaction session ran" "$( [ -n "$SID" ] && echo true || echo false )"

DRY="$SANDBOX/.claude/dry"
check "PreCompact(auto) snapshot written (*.jsonl.gz)" \
  "$( ls "$DRY"/snapshots/*-auto.jsonl.gz >/dev/null 2>&1 && echo true || echo false )"
check "auto-compact ledger marker appended (⚠ line)" \
  "$( grep -q 'auto-compact fired' "$DRY/ledger.md" 2>/dev/null && echo true || echo false )"
check "PostCompact summary audit written (*-summary-auto.md)" \
  "$( ls "$DRY"/snapshots/*-summary-auto.md >/dev/null 2>&1 && echo true || echo false )"

TRANSCRIPT_DIR="$HOME/.claude/projects/$(python3 -c "
import re; print(re.sub(r'[^A-Za-z0-9-]', '-', '$SANDBOX'))")"
check "rehydrate injected after compaction (Context was just compacted)" \
  "$( grep -rq 'Context was just compacted' "$TRANSCRIPT_DIR" 2>/dev/null && echo true || echo false )"
check "snapshot gunzips cleanly" \
  "$( gzip -t "$DRY"/snapshots/*-auto.jsonl.gz 2>/dev/null && echo true || echo false )"

DBG="/tmp/claude-dry/debug.log"
if [ -f "$DBG" ]; then echo "--- debug.log tail ---"; tail -5 "$DBG"; fi
echo; echo "compact smoke: $PASS passed, $FAIL failed (sandbox kept: $SANDBOX)"
[ "$FAIL" -eq 0 ]
