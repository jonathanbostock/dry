# Native Context Management in Claude Code v2.1.x and Claude API
## Definitive Feature Map (August 2026)

---

## Summary

Claude Code v2.1.241 and the Claude platform (as of August 2026) offer a sophisticated native context management stack spanning session persistence, automatic compaction, multi-layer memory, checkpointing, and API-level context editing. However, gaps exist between what's automatic (good for most cases) and what's controllable (critical for long, unpredictable sessions). The plugin should target selective agent-initiated context management, fine-grained preservation rules, and the integration of API-level context editing into Claude Code's interactive loop.

---

## Feature Map

### 1. `/compact` Command and Auto-Compaction

**Trigger & Mechanics:**
- `/compact [instructions]` manually triggers conversation summarization (Claude Code CLI, v2.1.x)
- Auto-compaction fires when context approaches ~90% of the window size (heuristic; exact threshold varies by model)
- Custom instructions argument: pass a focus like `/compact focus on API changes` to guide what the summary preserves
- Emits `compact_boundary` event in session transcripts with `preTokens` field showing context size before compaction

**What Survives:**
- Recent user messages and Claude's responses (last N exchanges, where N depends on available tokens)
- CLAUDE.md files (reloaded from disk post-compaction)
- Nested CLAUDE.md files and path-scoped rules (reload as Claude reads matching files afterward)
- Auto memory (MEMORY.md, first 200 lines or 25KB reloaded)
- Recent file reads (cached, only expire on timeout)

**What Is Lost:**
- Detailed instructions from early conversation not in CLAUDE.md
- Full verbosity of early debugging sessions
- Tool outputs older than ~2–3 turns back (cleared automatically before summarization)

**Configuration:**
- `--autocompact <auto|tokens>`: sets auto-compact window (e.g., `--autocompact 500k`)
- `autoCompactEnabled` setting (default: true)
- `autoCompactWindow` setting (default: auto-computed per model)
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` env var: override the % threshold (undocumented; used internally)

**Source:** 
- Claude Code help text: `--autocompact <auto|tokens>`
- Docs: https://code.claude.com/docs/en/context-window.md, https://code.claude.com/docs/en/how-claude-code-works.md

**Version/Date:** Native since Claude Code v2.1.x (widespread in v2.1.200+); compaction boundaries logged since v2.1.210+

---

### 2. Automatic Tool-Result Clearing ("Microcompaction")

**What It Does:**
Before summarizing the full conversation, Claude Code automatically clears the oldest tool results (file contents, command outputs, search results) to free space. Replaces each with a placeholder so Claude knows it was removed. This is **not** context editing (API feature); it's a built-in heuristic in the client.

**Trigger:**
- When context usage exceeds ~70% of window and the next turn would push it over the limit
- Prioritizes clearing the oldest tool results first, preserving recent exchanges and CLAUDE.md

**Preservation:**
- Does NOT affect conversation messages or tool calls (input parameters)
- Only clears tool result outputs
- Preserves CLAUDE.md, auto memory, recent file reads

**Configurable:** 
- Not directly user-configurable; behavior is hardcoded in Claude Code
- API-level control via context editing (see section 4)

**Known Weaknesses:**
- No semantic understanding: oldest ≠ least important (a key diagnostic output from turn 3 might be critical)
- No agent control: agent cannot request to preserve a specific tool result
- Silent: agent sees the placeholder but has no way to signal "wait, I need that data back"

**Source:** https://code.claude.com/docs/en/how-claude-code-works.md

**Version/Date:** v2.1.x (exact version of introduction unclear; widely reported in v2.1.190+)

---

### 3. /context Command

**What It Shows:**
- Current token usage (tokens used / total window)
- List of loaded memory files (CLAUDE.md, CLAUDE.local.md, MEMORY.md)
- MCP tool names and deferred schemas
- Recent conversation size estimate
- Breakdown of system overhead vs. conversation vs. tool results

**No Interactive Controls:** Read-only diagnostic. Does not directly compact, clear, or dump context.

**Source:** https://code.claude.com/docs/en/context-window.md

---

### 4. Context Window Sizes and Models (August 2026)

| Model | Window | Max Output | Release | Status |
|-------|--------|-----------|---------|--------|
| Claude Fable 5 | 1M tokens | 128k tokens | June 2026 | Latest, general release |
| Claude Opus 5 | 1M tokens | 128k tokens | May 2026 | General release |
| Claude Sonnet 5 | 1M tokens | 128k tokens | Aug 2025 | General release |
| Claude Haiku 4.5 | 200k tokens | 64k tokens | Oct 2025 | Fast, general release |
| Claude Opus 4.8 | 1M tokens | 128k tokens | Legacy | Deprecated on API; available Bedrock/Foundry |
| Claude Opus 4.7 | 1M tokens | 128k tokens | Legacy | Deprecated on API; available Bedrock/Foundry |

**Tokenizer Note:** Claude Fable 5, Opus 5, Sonnet 5 use a new tokenizer (post-Opus 4.7) that produces ~30% more tokens for the same text vs. earlier models.

**Default in Claude Code CLI:** Depends on subscription; Fable 5 for most, Opus 5 for Max plan users.

**1M Context Options:** All current-tier models (Fable 5, Opus 5, Sonnet 5) support 1M. No special flag needed.

**Source:** https://platform.claude.com/docs/en/about-claude/models/overview (August 2026)

---

### 5. `/rewind` and Checkpointing

**Automatic Tracking:**
- Every user prompt creates a checkpoint
- Claude Code keeps file snapshots for the 100 most recent checkpoints per session
- Old snapshots automatically deleted (but file edit tracking stays indefinite)
- Checkpoints persist across session resume (tied to session ID)
- Deleted after 30 days (configurable via `cleanupPeriodDays` in settings)

**`/rewind` Menu Options:**
- **Restore code and conversation:** revert to a prior checkpoint (code + messages)
- **Restore conversation:** keep current code, rewind messages only
- **Restore code:** rewind edits, keep conversation history
- **Summarize from here:** compress forward (this point onward)
- **Summarize up to here:** compress backward (before this point)

**Limitations:**
- Does NOT track bash-command-caused file changes (only direct file edits via Claude's Edit tool)
- Does NOT track symlinked or hard-linked file restores (warning issued, files skipped)
- Does NOT track subagent edits unless the subagent runs in foreground (fork with `context: fork`)
- Checkpoints are session-local; separate sessions have separate checkpoints

**Source:** https://code.claude.com/docs/en/checkpointing.md

---

### 6. Session Resume and Branching

**Resume Semantics:**
- `claude --continue`: resume most recent session in current dir
- `claude --resume [name]`: interactive picker or resume by name/session-id
- `claude --resume <session-id>`: resume by ID from any directory
- `/resume` inside session: switch to different conversation
- `--fork-session` flag: creates new session ID instead of appending to original

**What a Resumed Session Restores:**
- Full conversation history
- Model setting (unless overridden with `--model` or `ANTHROPIC_MODEL` env)
- Agent definition (if session had `--agent`)
- Permission mode (except `plan` and `bypassPermissions`, which reset)
- Active goal (if one was set)
- Scheduled tasks (if not expired)

**What Does NOT Restore:**
- `--mcp-config`, `--settings`, `--plugin-dir`, `--add-dir` (must be re-passed)
- Directories added mid-session with `/add-dir` (unless re-added)

**Resume from Summary (Pro/Max Plans):**
When resuming a session inactive >1 hour and >100k tokens:
- Dialog offers: "Resume from summary" (runs `/compact`), "Resume full session", or "Don't ask again"
- Saves tokens on subsequent requests but loses fine details outside the summary

**Source:** https://code.claude.com/docs/en/sessions.md

---

### 7. CLAUDE.md Hierarchy and Auto Memory

**CLAUDE.md Load Order (Broadest to Most Specific):**
1. Managed policy (org-wide): `/etc/claude-code/CLAUDE.md` (Linux), `/Library/Application Support/ClaudeCode/CLAUDE.md` (macOS), `C:\Program Files\ClaudeCode\CLAUDE.md` (Windows)
2. User-level: `~/.claude/CLAUDE.md`
3. Project root or `./.claude/CLAUDE.md`
4. `./CLAUDE.local.md` (personal, git-ignored)
5. Nested `./subdir/CLAUDE.md` files (lazy-loaded when Claude reads matching files)
6. Path-scoped rules in `.claude/rules/` (via frontmatter `paths:`)

**All files concatenated, in order.** Later entries appear after earlier ones, so project-level overrides user-level.

**Size Guidelines:**
- Target <200 lines per file (larger files consume more context and reduce adherence)
- Files >4 MiB are skipped entirely
- CLAUDE.md imports via `@path/to/file` syntax (resolves relative to the CLAUDE.md, supports up to 4 hops)

**Auto Memory (MEMORY.md):**
- Claude-written, per-project directory at `~/.claude/projects/<project>/memory/`
- First 200 lines or 25KB of MEMORY.md loaded at session start (rest available on demand)
- Separate topic files (user_role.md, feedback_testing.md, etc.) loaded on-demand
- Shared across all worktrees of the same repo
- Types: `user` (role/preferences), `feedback` (corrections), `project` (ongoing work), `reference` (external info)
- Enabling: `autoMemoryEnabled` setting (default: true) or `/memory` toggle
- Custom directory: `autoMemoryDirectory` setting (supports `~/` expansion)

**Lazy-Loading Rules:**
- `.claude/rules/*.md` files load at startup (unless path-scoped)
- Path-scoped rules (`paths:` frontmatter) load only when Claude reads matching files
- Nested rules in subdirectories load on demand when Claude reads in that directory

**Source:** https://code.claude.com/docs/en/memory.md

---

### 8. Subagents and Task Tool (Context Isolation)

**Isolation Mechanics:**
Each subagent (created via `Task` tool, `/skill`, custom agent) gets:
- Fresh context window (no parent conversation history)
- Independent auto memory (if enabled for that agent)
- Only explicitly loaded CLAUDE.md from the project
- Git status snapshot (at parent session start)
- Preloaded skills from agent config

**What Does NOT Load in Subagent:**
- Parent's conversation history or tool results
- Parent's auto memory (exception: `/branch` fork inherits parent's full context)
- Output style preferences
- Session-specific settings (unless re-passed)

**Auto-Compaction in Subagents:**
- Same rules as main conversation
- Triggers on same thresholds
- Emits `compact_boundary` messages in subagent's separate transcript

**Benefit for Long Sessions:**
Subagent contexts reset per task, so large transcripts from prior work don't accumulate in the main conversation. Only the subagent's summary is returned to the parent.

**Source:** https://code.claude.com/docs/en/sub-agents.md

---

### 9. API-Level Context Management

#### 9.1 Memory Tool (API Feature)

**Type:** Client-side tool (`memory_20250818`)
**Release:** August 2025 (date of type string); stable as of August 2026
**Status:** Stable, not beta

**What It Does:**
Claude stores and retrieves information in `/memories/` directory (your backend). Just-in-time context retrieval: agent reads memory files on demand instead of loading all at startup, keeping active context lean.

**Commands:**
- `view /memories/[path]`: list directory or read file with line numbers
- `create /memories/file.md`: write new file
- `str_replace /memories/file.md`: replace text substring
- `insert /memories/file.md`: insert line at position
- `delete /memories/path`: delete file or directory
- `rename /memories/old /memories/new`: move/rename

**How It Works:**
- Agent calls memory tool (client-side)
- Your handler executes file operations on your backend storage
- Agent reads result, continues reasoning

**Security:** Path traversal protection required (validate paths start with `/memories`, resolve to canonical form)

**Integration Notes:**
- Must be explicitly enabled (pass `tools: [{"type": "memory_20250818", "name": "memory"}]`)
- Does NOT auto-load into Claude Code CLI (API-only feature)
- SDKs provide helpers: `BetaLocalFilesystemMemoryTool` (Python/TypeScript), `BetaMemoryTool20250818` (Java), etc.

**Source:** https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool

---

#### 9.2 Context Editing (API Beta Feature)

**Type:** Server-side context management
**Release:** Beta, June 2025 (`context-management-2025-06-27` header)
**Status:** Beta (as of August 2026)

**Beta Header Required:**
```
anthropic-beta: context-management-2025-06-27
```

**Strategies:**

##### `clear_tool_uses_20250919`
- Clears old tool results (file contents, search output, etc.) when context grows beyond threshold
- Replaces with placeholder text
- Optional `clear_tool_inputs: true` to also clear tool call parameters
- Preserves conversation messages and CLAUDE.md
- Best for agentic workflows with heavy tool use

##### `clear_thinking_20251015`
- Manages `thinking` blocks (from extended thinking)
- Configurable to keep or clear aggressively
- Default: Opus 4.5+ keeps all, earlier versions keep last turn only

**API Usage Example:**
```python
client.beta.messages.create(
    model="claude-opus-5",
    messages=[...],
    tools=[...],
    betas=["context-management-2025-06-27"],
    context_management={"edits": [{"type": "clear_tool_uses_20250919"}]},
)
```

**Availability:**
- API-only (not integrated into Claude Code CLI as of v2.1.241)
- Works with all models supporting context management beta
- Does not destroy prompt cache (manages before caching)

**Source:** https://platform.claude.com/docs/en/build-with-claude/context-editing

---

### 10. Agent SDK Context Management

**Automatic Compaction:**
- Agent SDK (Python/TypeScript libraries) runs same compaction heuristic as Claude Code
- Triggers when context approaches limit
- Emits `SystemMessage` with `subtype: "compact_boundary"`

**Customization:**
- **CLAUDE.md "Summary instructions" section:** compactor reads and preserves intent (example: "always preserve decisions and reasoning")
- **`PreCompact` hook:** callback fires before compaction, can archive full transcript
- **Manual `/compact` command:** send as prompt string (treated as ordinary input)

**No Built-In Context Editing Integration:**
- Agent SDK does not expose `context-management-2025-06-27` beta or `clear_tool_uses` strategies
- Plugin or wrapper must add that layer if desired

**Subagent Isolation:**
- Each subagent has fresh context
- Auto-compaction works per subagent
- Parent's large transcripts don't accumulate in main loop

**Source:** https://code.claude.com/docs/en/agent-sdk/agent-loop.md

---

## What Claude Code Already Does Automatically

1. **Persistent Sessions:** Saves all transcripts locally, resumable indefinitely (within 30-day retention)
2. **Automatic Checkpointing:** Snapshots file state before each turn, rewindable via `/rewind`
3. **Multi-Layer Memory:** CLAUDE.md hierarchy + auto memory (MEMORY.md) loaded fresh each session
4. **Tool-Result Clearing:** Oldest tool outputs automatically cleared before conversation summarization
5. **Session-Level Auto-Compaction:** Summarizes conversation when context approaches limit, preserves recent exchanges and CLAUDE.md
6. **Lazy-Loading for Large Rules:** Path-scoped `.claude/rules/` files load only when matching files are read
7. **Subagent Context Isolation:** Each subagent starts with fresh context, only summary returned to parent
8. **Prompt Caching:** CLAUDE.md and tool schemas cached across turns, reducing token cost and latency

---

## Gap List: What Still Hurts in Long Sessions

### Critical Gaps

1. **Generic Summarization Without Agent Control:**
   - Auto-compaction preserves recent messages but uses generic summarization (model's best guess)
   - No agent-initiated signal: "summarize but preserve X, Y, Z" (only via CLAUDE.md "summary instructions", which are hints not guarantees)
   - Important decisions buried in verbose early conversation may be lost

2. **No Semantic Tool-Result Preservation:**
   - Tool results cleared by age, not importance
   - Clearing is silent; agent has no way to request "keep this output, it's critical"
   - Placeholder text tells agent data is gone, but no recovery mechanism

3. **Tool-Result Bloat Before Microcompaction Kicks In:**
   - Tool results accumulate through several turns before auto-clear fires
   - Long file reads or verbose command outputs can fill context even while conversation is short
   - Agent cannot selectively forget (e.g., "forget that large log file, I processed it")

4. **No Fine-Grained Context Editing in Claude Code CLI:**
   - Context editing (`clear_tool_uses_20250919`) is API-only, not integrated into Claude Code
   - Requires custom wrapper to access; not available interactively
   - Agent cannot request "clear tool results older than turn 5" mid-session

5. **Checkpoint Restore is All-or-Nothing:**
   - `/rewind` restores to a point, but cannot selectively restore specific files or keep others
   - Checkpointing does not track bash-command-induced file changes (only direct edits)
   - Symlinked/hard-linked files cannot be restored

6. **Memory Tool Not Integrated into Claude Code:**
   - Memory tool (`memory_20250818`) is API-only, not available in Claude Code CLI
   - Agent cannot use memory tool to offload context during a Claude Code session
   - Cross-session persistence requires custom wrapper

### Secondary Gaps

7. **Subagent Overhead in Parent Context:**
   - Only subagent's final summary returned; intermediate tool calls are not visible
   - If parent needs subagent's work state mid-way, must request re-invocation
   - No mechanism for subagent to save checkpoint back to parent

8. **No "Do Not Summarize" Regions:**
   - CLAUDE.md is preserved, but inline conversation instructions are not
   - Detailed debugging or decision context from conversation is summarized away without recovery

9. **Lazy-Loading Doesn't Extend to Tool Results:**
   - Path-scoped rules load on demand, but tool results always load on demand (no caching strategy)
   - Large, recently-used file reads are not replayed from cache if needed again

10. **Effort-Based Token Budgeting is Coarse:**
    - `--effort` controls reasoning depth but not context size directly
    - Agent cannot trade reasoning for context (e.g., "summarize now to free space, continue at low effort")

---

## Design Implications for Our Plugin

The plugin should focus on:

1. **Agent-Initiated Selective Compaction:**
   - Expose `/compact` with hints for what to preserve (not just free-form instructions)
   - Example: `/compact preserve-sections [DECISIONS, BUG_ANALYSIS, API_SCHEMA]` (marks important regions)
   - Agent can inspect context before requesting compaction

2. **Tool-Result Management:**
   - Expose command to mark tool results as "keep" (do not auto-clear) or "archive" (offload to memory)
   - Example: `/keep-tool-result <turn> <tool>` or `/archive-to-memory <turn>`
   - Integrate with memory tool (API) for persistent offload

3. **API-Level Context Editing Integration:**
   - If running on Claude API backend, expose `context-management-2025-06-27` controls via `/edit-context`
   - Example: `/edit-context clear-tool-uses-before-turn=10 strategy=aggressive`
   - Fallback to client-side tool clearing for non-API backends

4. **Checkpoint-Based Context Snapshots:**
   - Extend `/rewind` to save checkpoint to named file (for audit, replay, or re-use)
   - Example: `/checkpoint save debug-session-20260823-morning`
   - Allow resuming from checkpoint state

5. **Cross-Session Context Bridging:**
   - Integrate memory tool into Claude Code to persist learnings and decision logs
   - Auto-populate memory with summaries of closed sessions
   - Allow agent to query memory before starting new task

6. **Context Prediction and Early Compaction:**
   - Analyze current context growth rate
   - Proactively suggest compaction before hitting auto-threshold
   - Example: "Context will fill in ~5 turns; consider `/compact` now?"

---

## Sources

| Topic | URL | Verified |
|-------|-----|----------|
| Context Window Management | https://code.claude.com/docs/en/context-window.md | [verified] |
| How Claude Code Works | https://code.claude.com/docs/en/how-claude-code-works.md | [verified] |
| Memory (CLAUDE.md & Auto Memory) | https://code.claude.com/docs/en/memory.md | [verified] |
| Checkpointing | https://code.claude.com/docs/en/checkpointing.md | [verified] |
| Sessions | https://code.claude.com/docs/en/sessions.md | [verified] |
| Subagents | https://code.claude.com/docs/en/sub-agents.md | [verified] |
| Model Config & Auto-Compact | https://code.claude.com/docs/en/model-config.md | [verified] |
| Models Overview (Aug 2026) | https://platform.claude.com/docs/en/about-claude/models/overview | [verified] |
| Memory Tool (API) | https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool | [verified] |
| Context Editing (API Beta) | https://platform.claude.com/docs/en/build-with-claude/context-editing | [verified] |
| Agent SDK Agent Loop | https://code.claude.com/docs/en/agent-sdk/agent-loop.md | [verified] |
| Local Claude CLI Help | `claude --help` (v2.1.241) | [verified] |

---

**Report Date:** August 23, 2026  
**Researcher:** Claude (Haiku 4.5)  
**Target:** Claude memory management plugin design  
