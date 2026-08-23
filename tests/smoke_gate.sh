#!/usr/bin/env bash
# Live smoke for the SELF-COMPACTION GATE: with DRY_GATE=1 and a 100k
# auto-compact window, the gate defers auto-compacts, dry_watch tells the
# agent a compaction is pending, the agent releases it (touch compact-ok),
# and compaction proceeds at that boundary. Costs ~150k+ input tokens.
#
# Usage: bash tests/smoke_gate.sh   (exit 0 = all checks passed)
set -u

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$(mktemp -d /tmp/dry-smokeg-XXXXXX)"
MODEL="claude-fable-5"
PASS=0; FAIL=0
check() { if [ "$2" = "true" ]; then echo "PASS: $1"; PASS=$((PASS+1)); else echo "FAIL: $1"; FAIL=$((FAIL+1)); fi; }

echo "sandbox: $SANDBOX"
mkdir -p "$SANDBOX/.claude/dry"
printf '# dry ledger\n\n## Goal\nGate smoke test\n' > "$SANDBOX/.claude/dry/ledger.md"

GLOG="$SANDBOX/gate-invocations.log"
W="python3 $PLUGIN_ROOT/scripts/dry_watch.py"
GATE="sh -c 'date +%s >> $GLOG; DRY_GATE=1 exec python3 $PLUGIN_ROOT/scripts/dry_gate.py'"
C="python3 $PLUGIN_ROOT/scripts/dry_checkpoint.py"
R="python3 $PLUGIN_ROOT/scripts/dry_rehydrate.py"

cat > "$SANDBOX/.claude/settings.json" <<EOF
{
  "hooks": {
    "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "$W", "timeout": 10 } ] } ],
    "PostToolUse": [ { "matcher": "*", "hooks": [ { "type": "command", "command": "$W", "timeout": 10 } ] } ],
    "PreCompact": [ { "hooks": [
      { "type": "command", "command": "$GATE", "timeout": 15 },
      { "type": "command", "command": "$C", "timeout": 60 }
    ] } ],
    "PostCompact": [ { "hooks": [ { "type": "command", "command": "$C", "timeout": 30 } ] } ],
    "SessionStart": [ { "matcher": "compact|clear|resume", "hooks": [ { "type": "command", "command": "$R", "timeout": 10 } ] } ]
  }
}
EOF

python3 - "$SANDBOX" <<'EOF'
import sys
for i in range(1, 10):
    with open(f"{sys.argv[1]}/doc{i}.txt", "w") as f:
        f.write("\n".join(
            f"doc{i} line {j}: gate smoke filler {i}-{j} with padding text"
            for j in range(1600)))
EOF

PROMPT="Read these files one at a time, each with a single Read call, in order: doc1.txt through doc9.txt in $SANDBOX. After each, state just its number. IMPORTANT: if at any point a [dry] message tells you a gated auto-compact is pending, immediately follow its instruction (run the touch command it gives, one Bash call) and then continue with the remaining files. Finish by replying: ALLDONE."
OUT="$(cd "$SANDBOX" && timeout 540 claude -p "$PROMPT" \
  --model "$MODEL" --autocompact 100000 --output-format json 2>&1)"
LAST="$(printf '%s\n' "$OUT" | awk '/^\{/{l=$0} END{print l}')"
SID="$(printf '%s' "$LAST" | jq -r '.session_id // empty' 2>/dev/null)"
check "gate session ran" "$( [ -n "$SID" ] && echo true || echo false )"

TD="$HOME/.claude/projects/$(python3 -c "
import re; print(re.sub(r'[^A-Za-z0-9-]', '-', '$SANDBOX'))")"

INVOCATIONS=$(wc -l < "$GLOG" 2>/dev/null || echo 0)
BOUNDARIES=$(grep -h compact_boundary "$TD"/*.jsonl 2>/dev/null | wc -l)
echo "gate invocations: $INVOCATIONS, compactions: $BOUNDARIES"
check "gate invoked at least twice (block then retry/release)" \
  "$( [ "$INVOCATIONS" -ge 2 ] && echo true || echo false )"
check "at least one attempt was blocked (invocations > compactions)" \
  "$( [ "$INVOCATIONS" -gt "$BOUNDARIES" ] && echo true || echo false )"
check "pending notice delivered to agent" \
  "$( grep -rq 'gated auto-compact is pending' "$TD" 2>/dev/null && echo true || echo false )"
check "agent released the gate (touch compact-ok in transcript)" \
  "$( grep -rq 'touch .claude/dry/compact-ok\|touch \.claude/dry/compact-ok\|compact-ok' "$TD" 2>/dev/null && echo true || echo false )"
check "compaction proceeded after release (boundary present)" \
  "$( [ "$BOUNDARIES" -ge 1 ] && echo true || echo false )"
check "markers cleaned up (no lingering compact-ok / compact-pending)" \
  "$( [ ! -e "$SANDBOX/.claude/dry/compact-ok" ] && [ ! -e "$SANDBOX/.claude/dry/compact-pending" ] && echo true || echo false )"
check "pre-compact snapshot written" \
  "$( ls "$SANDBOX"/.claude/dry/snapshots/*.jsonl.gz >/dev/null 2>&1 && echo true || echo false )"

DBG="/tmp/claude-dry/debug.log"
if [ -f "$DBG" ]; then echo "--- debug.log tail ---"; tail -3 "$DBG"; fi
echo; echo "gate smoke: $PASS passed, $FAIL failed (sandbox kept: $SANDBOX)"
[ "$FAIL" -eq 0 ]
