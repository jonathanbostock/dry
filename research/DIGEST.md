# Claude Code Plugin API Reference — Digest

## Summary

Complete, build-ready API reference for Claude Code plugins on v2.1.241. Covers manifest schema, all hook event types (19+) with stdin/stdout JSON specs, context injection capability matrix, transcript format, skills, commands, packaging, and gotchas. Ground truth from live docs (code.claude.com/docs), verified against arch2 plugin and official marketplace.

---

## Key Findings

**[verified]** Plugin manifest (`plugin.json`) is optional if plugin ships only as directory; required for marketplace distribution. Name field is only required field.

**[verified]** Hook events fall into five categories: per-session (SessionStart/End), per-turn (UserPromptSubmit/Stop), tool execution (PreToolUse/PostToolUse), agent (SubagentStart/Stop), context/compaction (PreCompact/PostCompact), and others (19 total event types).

**[verified]** Exit code 2 = blocking error; blocks action on PreToolUse, UserPromptSubmit, Stop, PreCompact, UserPromptExpansion. Exit 0 = success; reads JSON from stdout for decisions. Other codes = non-blocking.

**[verified]** Hook configuration is **session-scoped cache** — changes to hooks/hooks.json mid-session do NOT reload; must restart session.

**[verified]** Hooks can inject `additionalContext` into Claude's conversation via JSON `hookSpecificOutput.additionalContext` field on SessionStart, PreToolUse, PostToolUse, PostToolUseFailure. SessionEnd, PermissionRequest, PermissionDenied cannot inject.

**[verified]** Transcript format: JSONL records at `~/.claude/projects/<munged-cwd>/<session_id>.jsonl`. Context usage computed from last assistant message's `message.usage.input_tokens`. Cache savings tracked in `cache_read_input_tokens` (not counted toward 200k limit).

**[verified]** Skills in plugins: SKILL.md frontmatter fields include `name`, `description`, `allowed-tools`, `disable-model-invocation`, `model`, `context: [fork]`, `append-system-prompt`, `once`. Model auto-invokes based on description trigger phrases; users invoke via `/plugin:skill-name`.

**[verified]** Marketplace installation: `/plugin install name@marketplace` interactive, `claude plugin install name@marketplace [--scope]` CLI. Local: `/plugin marketplace add /path` then install. Skills-dir auto-load: `claude plugin init name` → loads as `name@skills-dir` next session.

**[verified]** Plugin validation: `claude plugin validate ./plugin [--strict]`. Checks manifest syntax, hooks config, skill/agent frontmatter, unrecognized field names.

**[unverified]** PreCompact hook ability to inject/modify compaction prompt's `custom_instructions` field.

**[unverified]** Size limits for `additionalContext` in hook JSON output.

**[unverified]** SlashCommand tool: whether Claude can invoke slash commands itself; which commands are eligible (custom only? built-ins like `/compact` too?).

**[unverified]** Whether plugins can ship a statusline (vs. settings.json-only).

---

## Context-Injection Capability Matrix

| Event | Can Inject Context | Method | Size Limit |
|-------|---|---|---|
| SessionStart | ✓ | JSON `additionalContext` | [unverified] |
| PreToolUse | ✓ | JSON `additionalContext` | [unverified] |
| PostToolUse | ✓ | JSON `additionalContext` | [unverified] |
| PostToolUseFailure | ✓ | JSON `additionalContext` | [unverified] |
| UserPromptSubmit | ✓ | Plain text stdout | [unverified] |
| Stop | ✗ | — | — |
| PermissionRequest | ✗ | — | — |
| PermissionDenied | ✗ | — | — |
| PreCompact | [unverified] | Modify custom_instructions? | [unverified] |
| PostCompact | ✗ | — | — |
| SubagentStart | ✗ | — | — |
| Other events | ✗ | — | — |

---

## Design Implications

1. **Context Management via Hooks:** PreToolUse, PostToolUse, SessionStart can inject context; ideal for state checkpoints, compaction summaries, tool-result summaries (cleaning stale results).

2. **Blocking vs. Non-Blocking:** PreToolUse, UserPromptSubmit, Stop, PreCompact support exit-2 blocking. Use for permission gates. PostToolUse* events cannot block but can annotate.

3. **Hook Timing:** Hooks are evaluated once per session at startup; mid-session changes require restart. Use settings.json (or .claude/settings.json for project scope) for persistent hook config.

4. **Plugin Manifest Optionality:** Smallest plugin (one SKILL.md at root) doesn't need plugin.json; required only for marketplace distribution and component path customization.

5. **Skill Invocation Models:** `disable-model-invocation: true` makes skill manual-only (`/plugin:skill`). Default false allows Claude to auto-invoke based on description.

6. **Transcript-Driven Diagnostics:** Last assistant message's `usage` field gives live context consumption. Hook can read transcript_path, parse JSONL, compute context state, inject optimization directives.

---

## Open Questions

- Hook parallelism: Do multiple hooks on same event run sequentially or in parallel?
- Cost calculation: How are LSP plugins with missing metadata handled in token estimates?
- Compaction hook: Can PreCompact hook modify or only observe?
- SlashCommand tool: Is it available? Which commands eligible? Character budget?
- Statusline plugin: Can plugins ship statusline, or settings-only?
- Size bounds: `additionalContext` field size limits?

---

## Report

Full reference: **`/workspace/claude-memory-management/research/hooks-api.md`** (1459 lines)

Sections:
1. Plugin Anatomy (manifest schema, component dirs, env vars)
2. Hooks (19 events, stdin/stdout, exit codes, matchers, decision control, timeout defaults)
3. Transcript Format & Context Usage Computation
4. Skills in Plugins (SKILL.md frontmatter, token cost)
5. Slash Commands & SlashCommand Tool
6. Statusline Configuration
7. Packaging, Installation, Marketplace Schema
8. Gotchas & Known Behaviors
9. Version Information
10. Unverified / Open Questions
11. Sources

---

## Sources

- https://code.claude.com/docs/en/plugins.md
- https://code.claude.com/docs/en/plugins-reference.md
- https://code.claude.com/docs/en/hooks.md
- https://code.claude.com/docs/en/plugin-marketplaces.md
- https://code.claude.com/docs/en/discover-plugins.md
- https://code.claude.com/docs/en/skills.md

