#!/usr/bin/env bash
# Behavioural smoke for the gate WORDING (v0.2.0): the prompt says nothing
# about dry, gates or compaction. This repo is loaded as the plugin
# (--plugin-dir) and any installed copy is disabled for the session, so the
# agent sees exactly the shipped skill, advisories and pending notice. Under
# DRY_GATE=1 --autocompact 100000 with the guard off (so Reads inflate
# context), the agent hunts a planted NEEDLE line in each of eight ~41k-token
# files using only Read (two 25k-token pages per file), and should: never
# propose /compact or /clear to the user, release the gate itself after the
# pending notice, get compacted, be told by the rehydrate preamble that the
# reset was its own release — and still get every needle right.
# Costs ~300k+ input tokens (mostly cache reads).
#
# Usage: bash tests/smoke_gate_unprompted.sh   (exit 0 = all checks passed)
set -u

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$(mktemp -d /tmp/dry-smokeu-XXXXXX)"
MODEL="claude-fable-5"
PASS=0; FAIL=0
check() { if [ "$2" = "true" ]; then echo "PASS: $1"; PASS=$((PASS+1)); else echo "FAIL: $1"; FAIL=$((FAIL+1)); fi; }

echo "sandbox: $SANDBOX"
mkdir -p "$SANDBOX/.claude/dry"
printf '# Ledger — corpus documentation\n\n## Goal\nOne summary line per doc in summaries.md\n\n## Now\nstarting\n\n## Next\n1. read docs in order\n' > "$SANDBOX/.claude/dry/ledger.md"

python3 - "$SANDBOX" <<'PY'
import sys, json, random
rng = random.Random(20260924)
key = {}
for i in range(1, 9):
    needle = rng.randint(900, 1500)  # second Read page, so both pages are needed
    key[f"doc{i}"] = needle + 1      # 1-based line number as Read displays it
    with open(f"{sys.argv[1]}/doc{i}.txt", "w") as f:
        f.write("\n".join(
            (f"doc{i} line {j}: the NEEDLE is here" if j == needle
             else f"doc{i} line {j}: corpus filler {i}-{j} with padding text")
            for j in range(1600)))
json.dump(key, open(f"{sys.argv[1]}/needles.json", "w"))
PY

PROMPT="Each of doc1.txt through doc8.txt in $SANDBOX contains exactly one line with the word NEEDLE. Working strictly in order, find that line's number in each file using ONLY the Read tool to look at file contents (do not use Grep, Glob, Bash, or any other tool to search or read them; page through with offset/limit as needed). After each file, append one line to $SANDBOX/found.md of the form 'docN: <line number>'. When all eight lines are written, reply exactly: ALLDONE."
OUT="$(cd "$SANDBOX" && DRY_GATE=1 DRY_DISABLE_GUARD=1 timeout 570 claude -p "$PROMPT" \
  --model "$MODEL" --autocompact 100000 --output-format json \
  --settings '{"enabledPlugins":{"dry@dry":false}}' --plugin-dir "$PLUGIN_ROOT" 2>&1)"
LAST="$(printf '%s\n' "$OUT" | awk '/^\{/{l=$0} END{print l}')"
SID="$(printf '%s' "$LAST" | jq -r '.session_id // empty' 2>/dev/null)"
check "session ran" "$( [ -n "$SID" ] && echo true || echo false )"

TD="$HOME/.claude/projects/$(python3 -c "
import re; print(re.sub(r'[^A-Za-z0-9-]', '-', '$SANDBOX'))")"
F="$(ls -t "$TD"/*.jsonl 2>/dev/null | head -1)"

python3 - "$F" > "$SANDBOX/analysis.txt" <<'PY'
import json, re, sys
recs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
texts, releases, boundaries = [], 0, 0
for r in recs:
    if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
        boundaries += 1
    if r.get("type") != "assistant":
        continue
    for b in (r.get("message") or {}).get("content") or []:
        if b.get("type") == "text":
            texts.append(b.get("text") or "")
        elif b.get("type") == "tool_use" and b.get("name") == "Bash":
            if "compact-ok" in json.dumps(b.get("input")):
                releases += 1
bad = [t for t in texts if re.search(r"/compact|/clear|compaction point|% of (your|the|my) (context|window|budget)|context is (at|near|getting)", t, re.I)]
print(f"boundaries={boundaries}")
print(f"releases={releases}")
print(f"user_facing_compaction_talk={len(bad)}")
for t in bad[:3]:
    print("  BAD:", t[:200].replace("\n", " "))
PY
cat "$SANDBOX/analysis.txt"
val() { grep -m1 "^$1=" "$SANDBOX/analysis.txt" | cut -d= -f2; }

check "pending notice delivered (hook text, not prompt)" \
  "$( grep -q 'deferred it for you' "$F" 2>/dev/null && echo true || echo false )"
check "agent released the gate itself (touch compact-ok via Bash)" \
  "$( [ "$(val releases)" -ge 1 ] && echo true || echo false )"
check "compaction happened after release (compact_boundary present)" \
  "$( [ "$(val boundaries)" -ge 1 ] && echo true || echo false )"
check "no user-facing /compact, /clear or context-percentage talk" \
  "$( [ "$(val user_facing_compaction_talk)" -eq 0 ] && echo true || echo false )"
check "rehydrate preamble named the reset as the agent's own release" \
  "$( grep -q 'your own boundary release' "$F" 2>/dev/null && echo true || echo false )"
check "ledger got the ✓ released-at-a-boundary line (PostCompact)" \
  "$( grep -q 'compaction released at a boundary' "$SANDBOX/.claude/dry/ledger.md" 2>/dev/null && echo true || echo false )"
check "task completed with every needle correct (work survived the compactions)" \
  "$( python3 - "$SANDBOX" <<'PY'
import json, re, sys
sb = sys.argv[1]
key = json.load(open(f"{sb}/needles.json"))
try:
    found = dict(re.findall(r"^(doc\d+):\s*(\d+)", open(f"{sb}/found.md").read(), re.M))
except OSError:
    found = {}
ok = all(str(key[d]) == found.get(d) for d in key)
print("true" if ok else f"false (found={found})")
PY
)"

echo; echo "unprompted gate smoke: $PASS passed, $FAIL failed (sandbox kept: $SANDBOX)"
[ "$FAIL" -eq 0 ]
