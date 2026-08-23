# Claude Code Plugin & Hooks API Reference v2.1.241

Build-ready API specification for Claude Code plugins. Ground truth from v2.1.241 (`claude --version`), live docs at https://code.claude.com/docs/en/, and verified against real installed plugins (arch2, claude-plugins-official).

---

## 1. Plugin Anatomy

### 1.1 Manifest Schema (plugin.json)

Location: `.claude-plugin/plugin.json` inside plugin root (required if plugin is in a marketplace, optional if only shipped as directory)

**Full Schema:**
```json
{
  "$schema": "https://anthropic.com/claude-code/plugin.schema.json",
  "name": "plugin-name",
  "displayName": "Plugin Display Name",
  "version": "1.0.0",
  "description": "What this plugin does",
  "author": {
    "name": "Author Name",
    "email": "author@example.com",
    "url": "https://github.com/author"
  },
  "homepage": "https://docs.example.com",
  "repository": "https://github.com/owner/repo",
  "license": "MIT",
  "keywords": ["keyword1", "keyword2"],
  "defaultEnabled": false,
  "skills": ["./", "./custom/skills/"],
  "commands": ["./commands/"],
  "agents": ["./agents/"],
  "hooks": "./hooks/hooks.json",
  "mcpServers": "./.mcp.json",
  "lspServers": "./.lsp.json",
  "experimental": {
    "themes": "./themes/",
    "monitors": "./monitors.json"
  },
  "userConfig": {
    "api_endpoint": {
      "type": "string",
      "title": "API Endpoint",
      "description": "Your API endpoint",
      "required": false,
      "default": "https://api.example.com",
      "sensitive": false
    }
  },
  "channels": [
    {
      "server": "telegram",
      "userConfig": {}
    }
  ],
  "dependencies": [
    "helper-plugin",
    { "name": "secrets-vault", "version": "~2.1.0" }
  ],
  "metadata": {}
}
```

**Required Fields:**
- `name` (string, kebab-case): Unique identifier. Used as namespace prefix for skills: `plugin-name:skill-name`.

**Metadata Fields (optional):**
| Field | Type | Purpose |
|-------|------|---------|
| `displayName` | string | Human-readable name (falls back to `name`) |
| `version` | string | Semantic version; pins plugin to this release |
| `description` | string | Short description for marketplace |
| `author` | object | `{name, email, url}` |
| `homepage` | string | Documentation URL |
| `repository` | string | Source code URL |
| `license` | string | License identifier (MIT, Apache-2.0, etc.) |
| `keywords` | array | Discovery tags |
| `defaultEnabled` | boolean | Auto-enable when user hasn't configured (default: true) |

**Component Path Fields (optional):**
| Field | Type | Merge/Replace | Default | Notes |
|-------|------|---|---|---|
| `skills` | string\|array | **Adds** to default | `skills/` | Directories with `name/SKILL.md` format |
| `commands` | string\|array | **Replaces** default | `commands/` | Flat `.md` files (legacy; use `skills/`) |
| `agents` | string\|array | **Replaces** | `agents/` | Agent Markdown files |
| `hooks` | string\|array\|object | **Merges** | `hooks/hooks.json` | Can be inline object or file paths |
| `mcpServers` | string\|array\|object | **Merges** | `.mcp.json` | Can be inline or file path |
| `lspServers` | string\|array\|object | **Merges** | `.lsp.json` | Can be inline or file path |
| `experimental.themes` | string\|array | **Replaces** | `themes/` | Color theme definitions |
| `experimental.monitors` | string\|array | **Replaces** | `monitors.json` | Background monitor configs |

**Path Rules:**
- All paths must be relative to plugin root, start with `./`
- Exception: `skills` field accepts `"."` to mean plugin root (when `SKILL.md` is at root level)
- `skills` field always scans default `skills/` directory PLUS listed paths

**User Configuration:**
```json
{
  "userConfig": {
    "config_key": {
      "type": "string|number|boolean|directory|file",
      "title": "Display Label",
      "description": "Help text",
      "required": false,
      "default": "default_value",
      "sensitive": false,
      "multiple": false,
      "min": 0,
      "max": 100
    }
  }
}
```

- `sensitive: true` → stored in secure keychain, masked in UI
- `multiple: true` → allows array of strings (for `string` type only)
- `min`/`max` → numeric bounds (for `number` type only)
- User config stored in: `~/.claude/settings.json` under `pluginConfigs[<plugin-id>].options`
- Substitutable in skill/agent content, MCP/LSP configs, hook commands

### 1.2 Component Directory Structure

**Standard Layout:**
```
plugin-root/
├── .claude-plugin/
│   ├── plugin.json                  # Manifest (optional)
│   └── marketplace.json             # For publishing (optional)
├── SKILL.md                         # Single skill at root (alternative to skills/)
├── skills/
│   ├── skill-name/
│   │   ├── SKILL.md
│   │   └── scripts/                 # Supporting files
│   └── another-skill/
│       └── SKILL.md
├── commands/                        # Deprecated; use skills/
│   └── deploy.md
├── agents/
│   ├── reviewer.md
│   └── tester.md
├── hooks/
│   ├── hooks.json
│   └── hook-handlers/
│       └── on-session-start.ts
├── .mcp.json                        # MCP server configs
├── .lsp.json                        # LSP server configs
├── bin/                             # Executables added to Bash PATH
│   └── my-tool
├── monitors/
│   └── monitors.json
├── themes/
│   └── dracula.json
├── output-styles/
│   └── terse.md
├── settings.json                    # Default settings (only agent & subagentStatusLine)
├── package.json                     # Node.js dependencies
└── README.md                        # Documentation
```

**Component File Locations:**
| Component | Default Location | Required |
|-----------|---|---|
| Manifest | `.claude-plugin/plugin.json` | No |
| Skills | `skills/<name>/SKILL.md` | No |
| Single skill at root | `./SKILL.md` + `name` in frontmatter | No |
| Commands (deprecated) | `commands/*.md` | No |
| Agents | `agents/*.md` | No |
| Hooks | `hooks/hooks.json` | No |
| MCP servers | `.mcp.json` | No |
| LSP servers | `.lsp.json` | No |
| Monitors | `monitors/monitors.json` | No |
| Output styles | `output-styles/*.md` | No |
| Themes (experimental) | `themes/*.json` | No |
| Executables | `bin/*` | No |

**Important:** Don't place `commands/`, `agents/`, `skills/`, `hooks/` inside `.claude-plugin/` — they must be at plugin root.

### 1.3 Environment Variables

Available for path substitution in configs, skill/agent content, and hook commands:

| Variable | Resolves To | Use For |
|----------|-----------|---------|
| `${CLAUDE_PLUGIN_ROOT}` | Absolute path to plugin directory | Scripts, bundled config, binaries |
| `${CLAUDE_PLUGIN_DATA}` | `~/.claude/plugins/data/{plugin-id}/` | Persistent data (survives updates) |
| `${CLAUDE_PROJECT_DIR}` | Project root (`.git`, `.claude/` location) | Project-local scripts |

**Resolved in:**
- Skill/agent content: anywhere
- Hook commands: anywhere
- MCP stdio `command`, `args`, `env`
- MCP http/sse/ws `url`, `headers`
- LSP `command`, `args`, `env`, `workspaceFolder`

### 1.4 Plugin Hooks Integration

Hooks in plugins are declared in `hooks/hooks.json` and merge with user settings hooks. Plugin hooks fire at the same lifecycle events as user hooks but are scoped to that plugin.

**Hook merge behavior:**
- Plugin hooks + user hooks + project hooks all fire on the same events
- No deduplication; multiple hooks on same event all execute
- Plugin hooks cannot override user hooks
- Hook configuration caching is **session-scoped** — config changes mid-session do not reload hooks until next session

---

## 2. Hooks — Complete Reference

Hooks execute at lifecycle events in a session. They receive JSON stdin (command hooks) or HTTP POST body (HTTP hooks) and control session flow via exit codes and JSON output.

### 2.1 Hook Event List

**Per-Session Events:**
- `SessionStart` — Session begins or resumes
- `SessionEnd` — Session terminates
- `Setup` — During initialization (--init-only, --init, --maintenance)

**Per-Turn Events:**
- `UserPromptSubmit` — Before Claude processes user prompt
- `UserPromptExpansion` — When command expands to prompt (can block)
- `Stop` — When Claude finishes response
- `StopFailure` — When turn ends due to API error

**Tool Execution Events:**
- `PreToolUse` — Before tool executes (can block)
- `PostToolUse` — After tool succeeds
- `PostToolUseFailure` — After tool fails
- `PermissionRequest` — Tool needs permission (can decide)
- `PermissionDenied` — Auto mode denied tool call
- `PostToolBatch` — After parallel tool calls resolve

**Agent/Task Events:**
- `SubagentStart` — Subagent spawns
- `SubagentStop` — Subagent finishes
- `TaskCreated` — Task created via TaskCreate
- `TaskCompleted` — Task marked completed
- `TeammateIdle` — Agent team mate idles

**Context/Config Events:**
- `InstructionsLoaded` — CLAUDE.md or `.claude/rules/*.md` loads
- `ConfigChange` — Configuration file changes (hooks, settings)
- `CwdChanged` — Working directory changes
- `DirectoryAdded` — Directory added via `/add-dir`
- `FileChanged` — Watched file changes on disk

**Compaction Events:**
- `PreCompact` — Before context compaction
- `PostCompact` — After compaction

**MCP Events:**
- `Elicitation` — MCP server requests user input
- `ElicitationResult` — User responds to MCP elicitation

**Other Events:**
- `Notification` — Claude Code sends a notification
- `MessageDisplay` — While assistant message streams
- `WorktreeCreate` — Worktree created
- `WorktreeRemove` — Worktree removed

### 2.2 Common Input Fields (All Events)

```json
{
  "session_id": "string (uuid)",
  "prompt_id": "string (uuid, absent until first user input)",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "permission_mode": "default|plan|acceptEdits|auto|dontAsk|bypassPermissions",
  "effort": {
    "level": "low|medium|high|xhigh|max"
  },
  "hook_event_name": "EventName",
  "agent_id": "uuid (subagent-scoped events only)",
  "agent_type": "string (subagent-scoped events only)"
}
```

### 2.3 Per-Event Schemas

#### SessionStart
**Matchers:** `startup`, `resume`, `clear`, `compact`, `fork`

**Additional Input:**
```json
{
  "session_start_type": "startup|resume|clear|compact|fork"
}
```

**Exit Code 2 Behavior:** Non-blocking; shows stderr to user only

**Output Handling:**
- Plain text stdout added to context Claude can see
- JSON `additionalContext` injected into Claude's next turn
- HTML/structured output: JSON only

#### SessionEnd
**Matchers:** `clear`, `resume`, `logout`, `prompt_input_exit`, `other`

**Additional Input:**
```json
{
  "session_end_type": "string"
}
```

**Exit Code 2 Behavior:** Non-blocking; shows stderr

#### UserPromptSubmit
**Matcher:** None (always fires)

**Additional Input:**
```json
{
  "user_input": "string (the prompt text)",
  "system_prompt": "string (optional, from CLAUDE.md)",
  "conversation_count": number
}
```

**Exit Code 2 Behavior:** **Blocks** prompt processing and erases it

**Output Handling:**
- Plain text stdout added as context Claude can see
- JSON decision: `block: true` with `reason` field

**Timeout Default:** 30 seconds (lowered from 600)

#### UserPromptExpansion
**Matcher:** command name (e.g., `commit` matches `/commit-commands:commit`)

**Additional Input:**
```json
{
  "command_name": "string",
  "command_args": ["array", "of", "strings"],
  "expanded_prompt": "string"
}
```

**Exit Code 2 Behavior:** Blocks the expansion

**Output:** JSON `block: true` with `reason`

#### PreToolUse
**Matcher:** tool name (Bash, Edit, Write, Read, Grep, Glob, WebFetch, WebSearch, MCP tools, etc.)

**Additional Input:**
```json
{
  "tool_name": "Bash|Edit|Write|Read|Grep|Glob|WebFetch|WebSearch|Skill|SlashCommand|...",
  "tool_input": {
    // Tool-specific fields, e.g. for Bash:
    "command": "npm test",
    "description": "optional description",
    "timeout": 120000,
    "run_in_background": false
  },
  "tool_use_id": "toolu_01ABC123..."
}
```

**Exit Code 2 Behavior:** **Blocks** the tool call

**Permission Decision Output:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow|deny|escalate",
    "permissionDecisionReason": "string"
  }
}
```

**Input Modification:**
```json
{
  "hookSpecificOutput": {
    "updatedInput": {
      "command": "modified_command"
    }
  }
}
```

**Context Injection:**
```json
{
  "hookSpecificOutput": {
    "additionalContext": "Context to append to Claude's turn"
  }
}
```

#### PostToolUse
**Matcher:** tool name

**Additional Input:**
```json
{
  "tool_name": "string",
  "tool_input": { /* ... */ },
  "tool_use_id": "string",
  "tool_result": "string (the tool's output)"
}
```

**Exit Code 2 Behavior:** Non-blocking; shows stderr to user

**Output:** Can use `additionalContext` to inject context about the result

#### PostToolUseFailure
**Matcher:** tool name

**Additional Input:**
```json
{
  "tool_name": "string",
  "tool_input": { /* ... */ },
  "tool_use_id": "string",
  "error": "string (the error message)"
}
```

**Exit Code 2 Behavior:** Non-blocking; shows stderr

**Output:** Can use `additionalContext` or `systemMessage`

#### PermissionRequest
**Matcher:** tool name

**Additional Input:**
```json
{
  "tool_name": "string",
  "tool_input": { /* ... */ },
  "tool_use_id": "string",
  "permission_mode": "string"
}
```

**Exit Code 2 Behavior:** Ignored; use JSON decision only

**Decision Output:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": "allow|deny|escalate"
  }
}
```

#### PermissionDenied
**Matcher:** tool name

**Additional Input:**
```json
{
  "tool_name": "string",
  "tool_input": { /* ... */ },
  "denial_reason": "string",
  "permission_mode": "string"
}
```

**Exit Code 2 Behavior:** Ignored; use JSON `retry` field

**Output:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionDenied",
    "retry": true  // Allow model to retry this denied call
  }
}
```

#### Stop
**Matcher:** None (always fires)

**Additional Input:**
```json
{
  "last_assistant_message": "string (final response text)",
  "stop_reason": "string",
  "effort": { "level": "string" }
}
```

**Exit Code 2 Behavior:** **Blocks** stopping; conversation continues

**Output:** Can use `systemMessage` to show user message

#### SubagentStart
**Matcher:** agent type (e.g., `Explore`, `Plan`, custom agent name)

**Additional Input:**
```json
{
  "agent_type": "string (Explore|Plan|general-purpose|custom name)",
  "agent_id": "string (uuid)",
  "instructions": "string (optional)"
}
```

**Exit Code 2 Behavior:** Non-blocking; shows stderr only

#### SubagentStop
**Matcher:** agent type

**Additional Input:**
```json
{
  "agent_type": "string",
  "agent_id": "string",
  "stop_reason": "string"
}
```

**Exit Code 2 Behavior:** Non-blocking; shows stderr

#### Notification
**Matcher:** notification type (e.g., `permission_prompt`, `idle_prompt`, `auth_success`, `elicitation_*`, `agent_*`)

**Additional Input:**
```json
{
  "notification_type": "string",
  "notification_text": "string"
}
```

**Exit Code 2 Behavior:** Ignored (notifications always deliver)

#### FileChanged
**Matcher:** literal filename only (NOT regex) — e.g., `".env|.envrc"`, `"package.json"`

**Additional Input:**
```json
{
  "file_path": "string",
  "change_type": "created|modified|deleted"
}
```

**Exit Code 2 Behavior:** Non-blocking; shows stderr

**Note:** Matcher uses exact-match syntax only (letters, digits, `_`, `|`). Wildcards and regex NOT supported here.

#### ConfigChange
**Matcher:** config_source (e.g., `user_settings`, `project_settings`, `local_settings`, `policy_settings`, `skills`)

**Additional Input:**
```json
{
  "config_source": "user_settings|project_settings|local_settings|policy_settings|skills",
  "changed_keys": ["array", "of", "changed", "field", "names"]
}
```

**Exit Code 2 Behavior:** Blocks change from taking effect (except `policy_settings`)

#### InstructionsLoaded
**Matcher:** load_reason (e.g., `session_start`, `nested_traversal`, `path_glob_match`, `include`, `compact`)

**Additional Input:**
```json
{
  "file_path": "string (path to CLAUDE.md or rules/*.md)",
  "load_reason": "session_start|nested_traversal|path_glob_match|include|compact"
}
```

**Exit Code 2 Behavior:** Ignored

#### PreCompact
**Matcher:** compaction trigger (e.g., `manual`, `auto`)

**Additional Input:**
```json
{
  "compaction_trigger": "manual|auto",
  "current_token_count": number,
  "estimated_compact_token_count": number,
  "estimated_compact_message_count": number
}
```

**Exit Code 2 Behavior:** **Blocks** compaction

**Context Injection:** [UNVERIFIED] Can a PreCompact hook inject `custom_instructions` to modify the compaction prompt?

**Note:** Hook can observe compaction but should NOT modify the compaction process itself.

#### PostCompact
**Matcher:** None or compaction trigger

**Additional Input:**
```json
{
  "new_token_count": number,
  "tokens_removed": number
}
```

**Exit Code 2 Behavior:** Non-blocking; shows stderr

#### Elicitation
**Matcher:** MCP server name

**Additional Input:**
```json
{
  "mcp_server": "string",
  "tool_name": "string",
  "tool_use_id": "string",
  "elicitation_text": "string",
  "elicitation_type": "input|url"
}
```

**Exit Code 2 Behavior:** **Denies** the elicitation

#### ElicitationResult
**Matcher:** MCP server name

**Additional Input:**
```json
{
  "mcp_server": "string",
  "tool_name": "string",
  "tool_use_id": "string",
  "user_input": "string (or URL)"
}
```

**Exit Code 2 Behavior:** Blocks the response (action becomes decline)

### 2.4 Hook Configuration Format

**Location:**
- User level: `~/.claude/settings.json` under `hooks` key
- Project level: `.claude/settings.json` under `hooks` key
- Plugin level: `hooks/hooks.json` file (or inline in plugin.json)

**Full Schema:**
```json
{
  "hooks": {
    "EventName": [
      {
        "matcher": "tool_name|regex|specific_value",
        "if": "Tool(permission_rule)",
        "hooks": [
          {
            "type": "command|http|mcp_tool|prompt|agent",
            "command": "/path/to/script.sh",
            "args": ["arg1", "arg2"],
            "async": false,
            "timeout": 600,
            "statusMessage": "custom spinner message",
            "once": false,
            "url": "http://localhost:8080/hook",
            "headers": {
              "Authorization": "Bearer token"
            },
            "server": "mcp_server_name",
            "tool": "mcp_tool_name",
            "input": {
              "key": "${tool_input.command}"
            },
            "prompt": "Is this safe?",
            "model": "claude-opus"
          }
        ]
      }
    ]
  }
}
```

**Hook Configuration Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `matcher` | string | No | Filter when hooks fire (tool name, regex, or event-specific value); omit = all |
| `if` | string | No | Permission rule (e.g. `Bash(rm *)`, `Edit(*.ts)`); only for tool events |
| `hooks` | array | Yes | Array of hook handlers |
| `type` | string | Yes | One of: `command`, `http`, `mcp_tool`, `prompt`, `agent` |
| `command` | string | For command | Path to executable (supports `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PROJECT_DIR}`) |
| `args` | array | No | Command arguments (exec form, no shell interpretation) |
| `async` | boolean | No | Run hook asynchronously (default: false) |
| `timeout` | number | No | Seconds before canceling (default: 600) |
| `statusMessage` | string | No | Custom spinner message while running |
| `once` | boolean | No | Run once per session (skills only) |
| `url` | string | For HTTP | HTTP endpoint for hook |
| `headers` | object | No | HTTP headers (can use `allowedEnvVars`) |
| `allowedEnvVars` | array | No | Environment variables allowed in headers |
| `server` | string | For MCP tool | MCP server name |
| `tool` | string | For MCP tool | MCP tool name |
| `input` | object | No | MCP tool input (supports `${}` substitution) |
| `prompt` | string | For prompt/agent | Prompt text (supports `$ARGUMENTS`) |
| `model` | string | No | Model for prompt hooks (default: session model) |

**Matcher Pattern Rules:**

| Pattern | Evaluated As | Example |
|---------|--------------|---------|
| `"*"` or omitted | Match all | Fires on every occurrence |
| Alphanumeric + `_`, `-`, spaces, `,`, `\|` | Exact string match | `Bash`, `Edit\|Write`, `comma,separated` |
| Other characters | Unanchored JavaScript regex | `^Notebook.*`, `mcp__.*__write.*` |

**Tool Event Matchers:**
- Match against `tool_name` field
- Examples: `Bash`, `Edit|Write`, `Read`, `Grep`, `Glob`, `WebFetch`, `WebSearch`
- MCP tools: `mcp__<server>__<tool>` format
  - All from server: `mcp__memory__.*` (requires `.*`)
  - Plugin-scoped: `mcp__plugin_<plugin-name>_<server>__<tool>`

**Command Hook Form Options:**

*Exec form* (with `args` array):
```json
{
  "type": "command",
  "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/check.sh",
  "args": ["arg1", "arg2"]
}
```
- Path placeholder substituted as literal string
- No shell interpretation; safer for dynamic values

*Shell form* (without `args`):
```json
{
  "type": "command",
  "command": "npm run lint && npm test"
}
```
- Shell tokenization, pipes, `&&` supported
- Path placeholders need quoting: `"${CLAUDE_PROJECT_DIR}/hooks/check.sh"`

### 2.5 Hook Exit Code Semantics

| Exit Code | Behavior | JSON Required? |
|-----------|----------|---|
| **0** | Success; read decision/output from JSON (if valid) or plain text stdout | No |
| **2** | **Blocking error** — blocks action on events that support blocking (PreToolUse, UserPromptSubmit, etc.); message from JSON reason or stderr | No |
| **Other** (1, 3, etc.) | Non-blocking; action proceeds; hook treated as error | JSON can override on supporting events |

**Blocking Events** (exit 2 blocks):
- `PreToolUse` (blocks tool call)
- `UserPromptSubmit` (blocks prompt)
- `Stop` (blocks stopping)
- `PreCompact` (blocks compaction)
- `UserPromptExpansion` (blocks expansion)

**Non-Blocking Events** (exit 2 non-blocking; shows stderr):
- `PostToolUse`, `PostToolUseFailure`, `SessionStart`, `SessionEnd`, `SubagentStart`, etc.

### 2.6 Hook JSON Output Schema

**Universal Fields:**
```json
{
  "continue": true,
  "systemMessage": "string (shown in transcript to user)",
  "terminalSequence": "string (escape codes for terminal)",
  "hookSpecificOutput": {
    "hookEventName": "EventName",
    // Event-specific fields below
  }
}
```

**PreToolUse Output:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow|deny|escalate",
    "permissionDecisionReason": "string (required if deny)",
    "additionalContext": "string (injected into Claude's context)",
    "updatedInput": {
      "command": "modified_command",
      "file_path": "new/path"
    }
  }
}
```

**PermissionRequest Output:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": "allow|deny|escalate"
  }
}
```

**PermissionDenied Output:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionDenied",
    "retry": true
  }
}
```

**UserPromptSubmit Output:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "block": true,
    "reason": "string"
  }
}
```

**SessionStart / PostToolUse / etc. Output:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "EventName",
    "additionalContext": "string (injected into Claude's next turn)"
  }
}
```

### 2.7 Hook Timeout Defaults

| Event | Default Timeout |
|-------|---|
| Standard (PreToolUse, PostToolUse, SessionStart, etc.) | 600 seconds |
| `UserPromptSubmit` | 30 seconds |
| `MessageDisplay` | 10 seconds |
| Prompt hooks | 30 seconds |
| Agent hooks | 60 seconds |
| `SessionEnd` | Shared 1.5s budget (scaled, max 60s) |

Custom timeout via `"timeout": 120` in hook config (seconds).

### 2.8 HTTP Hook Response Handling

| Response | Behavior |
|----------|----------|
| 2xx with empty body | Success (no output) |
| 2xx with JSON object | Parsed using standard hook schema |
| 2xx with other body | Non-blocking error |
| Non-2xx status | Non-blocking error |
| Connection failure | Non-blocking error |
| Timeout | Hook canceled; no decision |

**Note:** HTTP hooks cannot signal blocking errors via status codes. Must return 2xx with JSON containing decision fields.

### 2.9 Context Injection Capability Matrix

Which hook events can inject context into Claude's conversation?

| Event | Can Inject | How | Size Limits |
|-------|-----------|-----|------------|
| `SessionStart` | YES | JSON `additionalContext` field | [UNVERIFIED] |
| `PreToolUse` | YES | JSON `additionalContext` field | [UNVERIFIED] |
| `PostToolUse` | YES | JSON `additionalContext` field | [UNVERIFIED] |
| `PostToolUseFailure` | YES | JSON `additionalContext` field | [UNVERIFIED] |
| `UserPromptSubmit` | YES | Plain text stdout | [UNVERIFIED] |
| `Stop` | NO | — | — |
| `PermissionRequest` | NO | — | — |
| `PermissionDenied` | NO | — | — |
| `PreCompact` | [UNVERIFIED] | Modify custom_instructions? | [UNVERIFIED] |
| `PostCompact` | NO | — | — |
| `SubagentStart` | NO | — | — |
| Other events | NO | — | — |

**[UNVERIFIED]:** No public docs on size limits for `additionalContext`; PreCompact hook's capability to inject/modify compaction prompt unconfirmed.

---

## 3. Transcript Format & Context Usage Computation

### 3.1 Transcript File Location & Structure

**Location:** `~/.claude/projects/<munged-cwd>/<session_id>.jsonl`

- `<munged-cwd>`: URL-safe encoding of absolute cwd path (hyphens separate segments)
- `<session_id>`: UUID of the session
- Format: JSONL (one JSON object per line)

**Example paths:**
- `/workspace/my-project` → `~/.claude/projects/-workspace-my-project/<session_id>.jsonl`

### 3.2 Transcript Record Types

Each JSONL line is a record with `type` field:

**User/Assistant Messages:**
```json
{
  "type": "user|assistant",
  "parentUuid": "uuid (parent record's uuid)",
  "isSidechain": false,
  "promptId": "uuid (conversation identifier)",
  "message": {
    "role": "user|assistant",
    "content": [
      {
        "type": "text",
        "text": "string"
      },
      {
        "type": "tool_use",
        "id": "toolu_...",
        "name": "Bash",
        "input": { /* tool input */ }
      }
    ]
  },
  "usage": {
    "input_tokens": number,
    "output_tokens": number,
    "cache_read_input_tokens": number,
    "cache_creation_input_tokens": number
  },
  "uuid": "uuid",
  "timestamp": "ISO8601 string",
  "sessionId": "uuid",
  "cwd": "/current/working/directory",
  "version": "2.1.241",
  "gitBranch": "HEAD|branch-name"
}
```

**Attachment Records (Hook Results, File Snapshots):**
```json
{
  "type": "attachment",
  "attachment": {
    "type": "hook_success|hook_additional_context|file_history_snapshot|...",
    "hookName": "SessionStart:startup",
    "hookEvent": "SessionStart",
    "content": "string|array",
    "stdout": "string",
    "stderr": "string",
    "exitCode": 0,
    "command": "command that ran",
    "durationMs": number
  },
  "uuid": "uuid",
  "parentUuid": "uuid",
  "timestamp": "ISO8601",
  "sessionId": "uuid"
}
```

**Goal/Status Records:**
```json
{
  "type": "goal_status|mode|permission-mode|last-prompt",
  "sessionId": "uuid",
  "goalCondition": "string (for goal_status)"
}
```

**File Snapshots:**
```json
{
  "type": "file-history-snapshot",
  "messageId": "uuid",
  "snapshot": {
    "messageId": "uuid",
    "trackedFileBackups": { /* file contents */ },
    "timestamp": "ISO8601"
  }
}
```

### 3.3 Computing Current Context Consumption

**Standard Trick** (used by statusline tools):

1. Find the last assistant message record in the main conversation chain (`isSidechain: false`)
2. Read its `message.usage` fields:
   - `input_tokens` (context consumed so far)
   - `output_tokens` (tokens in assistant's last response)
   - `cache_read_input_tokens` (from prompt cache, if enabled)
   - `cache_creation_input_tokens` (tokens written to cache)

3. **Current context usage** = `input_tokens` from last assistant message
4. **Prompt cache savings** = `cache_read_input_tokens` (not counted toward limit)

**Implementation sketch:**
```bash
jq -s '
  [.[] | select(.type == "assistant" and .isSidechain == false)] 
  | last 
  | .message.usage
' /path/to/transcript.jsonl
```

**Fields available:**
- `input_tokens`: tokens in context window (including system prompt, CLAUDE.md, conversation history)
- `output_tokens`: tokens generated in last assistant message
- `cache_read_input_tokens`: tokens served from prompt cache (not counted toward 200k limit)
- `cache_creation_input_tokens`: tokens written to cache (counted toward limit on first write)

---

## 4. Skills in Plugins

### 4.1 SKILL.md Frontmatter

**Location:** `skills/<name>/SKILL.md` or `commands/<name>.md` in plugin root

**Full Frontmatter Schema:**
```markdown
---
name: skill-name
description: When Claude should use this skill (include trigger phrases like "do X", "set up Y")
allowed-tools: Read, Write, Edit, Bash, Grep
disable-model-invocation: false
model: claude-opus
allowed-models: 
  - claude-opus
  - claude-sonnet-4
context: 
  - fork
append-system-prompt: true
once: false
model-invocation-priority: high
---

# Skill Title

Skill instructions and body...
```

**Frontmatter Fields:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | No | Folder/file name | Invocation name (becomes `/plugin:name`) |
| `description` | string | Yes | — | When Claude should use this; include trigger phrases |
| `allowed-tools` | string (comma/space separated) | No | All tools | Restrict to specific tools (Read, Write, Edit, Bash, Grep, Glob, WebFetch, WebSearch, Skill, SlashCommand, MCP tools, etc.) |
| `disable-model-invocation` | boolean | No | false | If true, Claude never auto-invokes; user invokes only |
| `model` | string | No | Session model | Override model for this skill |
| `allowed-models` | array | No | — | Whitelist of models that can invoke this skill |
| `context` | array | No | — | Context options: `fork` (isolate context, may not cost extra) |
| `append-system-prompt` | boolean | No | false | Append skill as system instruction (vs. user context) |
| `once` | boolean | No | false | Run once per session (skills only) |
| `model-invocation-priority` | string | No | — | `high`, `medium`, `low` (influence auto-invocation) |

**Notes:**
- Skill body (after frontmatter) is only loaded when invoked
- Skills in plugins are namespaced: `plugin-name:skill-name`
- Token cost of listing all skills in session is shown by `claude plugin details <plugin-name>`

### 4.2 Token Cost

Run to see projected cost:
```bash
claude plugin details plugin-name
```

Shows:
- Context cost estimate (tokens added per turn from listing skills, agents, MCP tools)
- Last updated date
- Components: skills, agents, hooks, MCP/LSP servers

---

## 5. Slash Commands & SlashCommand Tool

### 5.1 Slash Command Definition

**Location:** Skills at `skills/<name>/SKILL.md` or root `SKILL.md` with `name` frontmatter

**Invocation:** `/plugin-name:command-name [arguments]`

**Argument Passing:**

In skill content:
- `$ARGUMENTS` — all text after command name
- `$1`, `$2`, etc. — positional arguments
- `!`cmd`` — shell preprocessing (e.g., `!(git rev-parse --abbrev-ref HEAD)` expands to current branch)
- `@file` — references to files (expands to file path or content context)

**Example:**
```markdown
---
name: deploy
description: Deploy the application to staging or production
---

Deploy the \`$ARGUMENTS\` target using the standard pipeline.
Check out !`git rev-parse --abbrev-ref HEAD` to confirm branch.
```

### 5.2 SlashCommand Tool

**Can Claude invoke slash commands itself?** [UNVERIFIED] — The SlashCommand tool may be available as a tool that Claude can use, but eligibility criteria (custom only? built-ins like `/compact` too?) are not documented.

**Likely behavior:**
- Custom skills/commands: eligible for SlashCommand tool invocation
- Built-in commands (`/compact`, `/clear`, etc.): [UNVERIFIED] whether Claude can invoke them

**Character Budget:** [UNVERIFIED] whether SlashCommand tool has per-invocation budget

---

## 6. Statusline

### 6.1 Statusline Configuration

**Settings Key:** `settings.json` under `statusLine` object

**Schema:**
```json
{
  "statusLine": {
    "enabled": true,
    "template": "custom template string",
    "command": "/path/to/statusline-script.sh"
  }
}
```

**Stdin JSON Provided to Status Command:**
```json
{
  "model": "claude-opus",
  "workspace": "/current/project",
  "cost": {
    "session_cost_usd": 0.50,
    "session_input_tokens": 5000,
    "session_output_tokens": 2000
  },
  "context": {
    "current_tokens": 150000,
    "max_tokens": 200000,
    "exceeds_200k_tokens": false,
    "percent_used": 75
  },
  "transcript_path": "/path/to/transcript.jsonl"
}
```

**Plugin Statusline:** [UNVERIFIED] whether plugins can ship a statusline, or if it's settings-only

---

## 7. Packaging, Installation & Marketplace

### 7.1 Marketplace Schema (marketplace.json)

**Location:** `.claude-plugin/marketplace.json` in marketplace root

**Full Schema:**
```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "my-marketplace",
  "description": "Directory of my plugins",
  "owner": {
    "name": "Your Name",
    "email": "you@example.com"
  },
  "renames": {
    "old-name": "new-name"
  },
  "plugins": [
    {
      "name": "plugin-name",
      "description": "What it does",
      "author": {
        "name": "Author",
        "email": "author@example.com"
      },
      "category": "productivity|development|design|security|etc.",
      "homepage": "https://docs.example.com",
      "source": "./" | git source object,
      "version": "1.0.0"
    }
  ]
}
```

**Plugin Entry Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique plugin identifier (kebab-case) |
| `description` | string | Yes | Short description for marketplace |
| `author` | object | No | `{name, email, url}` |
| `category` | string | No | Marketplace category tag |
| `homepage` | string | No | Documentation URL |
| `source` | string or object | Yes | Plugin location (see below) |
| `version` | string | No | Semantic version pin |

**Source Types:**

*Relative path (same repo):*
```json
"source": "./"
```

*Git subdir:*
```json
"source": {
  "source": "git-subdir",
  "url": "https://github.com/org/repo.git",
  "path": "plugins/plugin-name",
  "ref": "v1.0.0",
  "sha": "commit-sha"
}
```

*Git URL:*
```json
"source": {
  "source": "url",
  "url": "https://github.com/org/repo.git",
  "sha": "commit-sha"
}
```

*URL (direct):*
```json
"source": {
  "source": "url",
  "url": "https://example.com/marketplace.json"
}
```

*Command source* (runs command to fetch plugin):
```json
"source": {
  "source": "command",
  "command": "your-command-here"
}
```

### 7.2 Installing Plugins

**From Marketplace (Interactive):**
```bash
/plugin install plugin-name@marketplace-name
```

**From Marketplace (CLI):**
```bash
claude plugin install plugin-name@marketplace-name [--scope user|project|local]
```

**From Local Directory:**
```bash
claude --plugin-dir ./my-plugin
```

**From Local or Remote URL:**
```bash
claude --plugin-url https://example.com/my-plugin.zip
```

### 7.3 Plugin Validation

```bash
claude plugin validate ./my-plugin
claude plugin validate ./my-plugin --strict
```

**Checks:**
- `plugin.json` syntax and schema compliance
- `hooks/hooks.json` configuration
- Skill/agent/command frontmatter
- Unrecognized field names (warnings by default, errors with `--strict`)
- Field type mismatches

### 7.4 Plugin Evaluation (Early Access)

**Availability:** Early access, enabled per organization. When not enabled, `claude plugin eval` prints "currently in early access" and exits 1.

**Basic Usage:**
```bash
claude plugin eval [target] [options]
```

**Case Format:**
```
evals/
└── my-case/
    ├── prompt.md          # Frontmatter: name, tags, plugins, runs, max_turns, timeout_seconds, allowed_tools, model, append_system_prompt, env
    ├── case.yaml          # (optional) schema_version, name, context.scaffold_script, context.history_file, context.add_dirs
    └── graders/
        ├── regex.md       # Frontmatter: type: regex, pattern, flags, match, target
        ├── tool_used.md   # type: tool_used, tool, input_match, min, max, arm
        ├── file_exists.md # type: file_exists, path
        └── llm.md         # type: llm, criteria, focus
```

**Key Options:**
- `--json [file.json]` — Output results as JSON
- `--report path` — Generate HTML report
- `--publish-report` — Publish to claude.ai artifacts
- `--threshold 0..1` — Pass threshold (default 1.0)
- `--allow-tools tool1,tool2` — Allow specific tools in sandbox
- `--ablation none|with-without` — Run baseline arm for comparison

[See embedded reference for full eval schema and grader types]

### 7.5 Local Installation from Directory

**Non-Interactive Plugin Dir:**
```bash
claude --plugin-dir /absolute/path/to/plugin
```

**Skills-Directory Auto-Load:**
```bash
claude plugin init my-tool
# Creates ~/.claude/skills/my-tool/.claude-plugin/plugin.json
# Loads automatically next session as my-tool@skills-dir
```

**Add Local Marketplace:**
```bash
/plugin marketplace add /path/to/marketplace
# or
/plugin marketplace add /path/to/marketplace.json
```

**Install from Local Marketplace:**
```bash
/plugin install plugin-name@local-marketplace-name
```

---

## 8. Gotchas & Known Behaviors

### 8.1 Hook Configuration Caching

- Hook configuration is **read once at session start** and cached
- Changes to `hooks/hooks.json` or `.claude/settings.json` mid-session do NOT reload hooks
- Workaround: Restart session or wait until next session to pick up hook changes

### 8.2 PreCompact Hook Limitations

- Hook CAN observe compaction event and context state
- Hook behavior re: custom_instructions injection: [UNVERIFIED]
- Cannot prevent the compaction itself (except via exit 2 blocking)

### 8.3 Plugin Skills vs. Standalone Skills

- Plugin skills: always namespaced (`/plugin-name:skill-name`)
- Standalone `.claude/skills/` : not namespaced (`/skill-name`)
- When same skill name exists in both contexts:
  - Standalone skill does NOT override plugin skill
  - Both remain available with different namespaces
  - No deduplication

### 8.4 MCP Tool Naming in Hooks

- Format: `mcp__<server>__<tool>` for direct tools
- Plugin-scoped: `mcp__plugin_<plugin-name>_<server>__<tool>`
- Matcher with regex: `mcp__memory__.*` (requires `.*`)
- [UNVERIFIED] whether plugin tools always scoped to plugin name

### 8.5 Context-Cost Estimation

- `claude plugin details <name>` shows estimated token cost
- Cost estimate for marketplace plugins may be missing if metadata not provided
- LSP plugins and theme/output-style plugins don't fully participate in cost calculation

### 8.6 Marketplace Auto-Update

- `claude-plugins-official` has auto-update enabled by default
- Third-party and local development marketplaces: auto-update disabled by default
- Can toggle per-marketplace: `/plugin` → Marketplaces tab → enable/disable auto-update
- Environment variable `DISABLE_AUTOUPDATER` disables all plugin updates
- Command-source plugins re-run once per session (separate from marketplace auto-update)

### 8.7 Plugin Dependencies

- Plugins can declare dependencies on other plugins
- Dependencies auto-install when parent plugin installs
- Version constraints use semver: `^`, `~`, exact versions
- Transitive dependencies auto-enable

### 8.8 Unrecognized Fields in plugin.json

- Claude Code **ignores** unrecognized top-level fields
- `claude plugin validate` warns about them (errors with `--strict`)
- Allows plugins to double as npm `package.json`, VS Code extensions, etc.

### 8.9 Hook Timeout Edge Cases

- `SessionEnd` uses shared 1.5s budget (scaled to longest timeout, max 60s)
- `MessageDisplay` hook timeout is 10s (lowered from 600s)
- Timeout cancels hook; no decision rendered; action proceeds (non-blocking by default)

### 8.10 Permission Modes in Hooks

Available modes (from `permission_mode` input field):
- `default` (labeled "Manual" in UI)
- `plan`
- `acceptEdits`
- `auto`
- `dontAsk`
- `bypassPermissions`

Hook can read mode but cannot change mode.

---

## 9. Version Information

- **CLI Version Tested:** 2.1.241
- **Documentation Source:** https://code.claude.com/docs/en/ (fetched 2026-08-23)
- **Live Plugin Example:** arch2 (0.9.2) at `/root/.claude/plugins/marketplaces/arch2/`
- **Verified Against:**
  - Official Anthropic marketplace (`claude-plugins-official`)
  - Real scaffolded plugin output (via `claude plugin init`)
  - Transcript JSONL format from active session

---

## 10. Unverified / Open Questions

- [ ] Exact size limits for `additionalContext` in hook JSON output
- [ ] PreCompact hook ability to inject or modify `custom_instructions` compaction prompt
- [ ] Whether SlashCommand tool is available as a tool Claude can invoke
- [ ] Which slash commands (built-in vs. custom) are eligible for SlashCommand tool
- [ ] SlashCommand tool character budget per invocation
- [ ] Whether plugins can ship a statusline (vs. settings-only)
- [ ] Exact plugin-scoping format for MCP tool names in hook matchers
- [ ] Cost calculation details for LSP plugins with no metadata
- [ ] Hook parallelism rules (do multiple hooks on same event run sequentially or in parallel?)

---

## 11. Sources

- **Claude Code Plugins Guide:** https://code.claude.com/docs/en/plugins.md
- **Plugins Reference (Full Schema):** https://code.claude.com/docs/en/plugins-reference.md
- **Hooks Reference:** https://code.claude.com/docs/en/hooks.md
- **Marketplace Guide:** https://code.claude.com/docs/en/plugin-marketplaces.md
- **Discover & Install Plugins:** https://code.claude.com/docs/en/discover-plugins.md
- **Skills Documentation:** https://code.claude.com/docs/en/skills.md
- **Real Installed Plugin (arch2):** `/root/.claude/plugins/marketplaces/arch2/.claude-plugin/plugin.json`
- **Official Marketplace:** `/root/.claude/plugins/marketplaces/claude-plugins-official/.claude-plugin/marketplace.json`
- **Scaffolded Plugin Template:** Output of `claude plugin init --with skills --with agents --with hooks`

