# Literature review: agentic memory & context management for a single-agent CLI coding assistant

Date: 2026-08-23. Scope: what transfers to a Claude Code plugin that lets the agent manage its own context window during long coding tasks (judgement-driven self-compaction, stale-tool-result cleanup, checkpointing so compaction// clear is lossless). Tags: [verified] = primary source fetched or multiply corroborated; [likely] = credible secondary source; [unverified] = single secondary source.

---

## Summary

The field converged in 2025-2026 on a small set of load-bearing ideas. (1) Context is a finite resource with diminishing marginal returns — degradation ("context rot") begins well before the window is full, so compaction should trigger early and be judged, not just thresholded. (2) The cheapest and safest context reduction is **reversible**: masking/pruning stale tool results while keeping a pointer (file path, command) to re-fetch them — this beats LLM summarization on cost and is loss-free; the one coding-agent-specific controlled study found tool-output masking was the *only* net-positive condensation strategy. (3) Lossy summarization is a last resort and must preserve a known checklist (intent, decisions + rationale, unresolved errors, file paths, pending work); the strongest systems externalize this into a **ledger file** (todo.md / NOTES.md) that is re-recited into recent context, doubling as attention steering and a lossless checkpoint across compaction or /clear. (4) Compact at **task boundaries** ("fold" a finished subtask into its outcome), not mid-stream — RL-trained folding agents beat blind summarization with 10x smaller active contexts. (5) Respect the KV-cache: append-only context, prune only at breakpoints, batch edits — sequence mutations cost more than the tokens they save (~10x cached-vs-uncached price difference). (6) Cross-session memory for a coding agent is best done with plain files in the repo plus reflection-style distilled lessons, updated as incremental deltas (never full rewrites, which cause "context collapse").

Prior art is substantial: Anthropic now ships server-side context editing, a compaction API, a memory tool, and Claude Code's own multi-layer compaction; plugins claude-mem and context-mode occupy adjacent niches. Nothing we found is exactly an **agent-judgement-driven, in-session self-compaction + ledger** plugin — the academic version of that idea (Context-Folding, AgentFold, ACE) uses trained models or bespoke harnesses; the plugin versions are automatic/harness-side rather than judgement-driven. Our plan's niche is real but narrow: the value-add is the judgement layer and the lossless ledger, built on top of (not against) native mechanisms.

---

## Industry engineering lessons

### Anthropic — "Effective context engineering for AI agents" (Sep 2025) [verified]
Defines context engineering as "curating and maintaining the optimal set of tokens during LLM inference"; treats attention as a budget depleted by n² token relationships ("context rot ... a finite resource with diminishing marginal returns"). Concrete techniques for long-horizon work: **compaction** (summarize and reinitiate, preserving "architectural decisions, unresolved bugs, and implementation details while discarding redundant tool outputs"; "the lightest touch is clearing tool results once processed"); **structured note-taking / agentic memory** (NOTES.md, TODO lists persisted outside the window, retrieved after resets); **sub-agent isolation** (exploration in a separate window, 1-2k-token condensed returns); **just-in-time retrieval** via lightweight identifiers (paths, URLs) instead of pre-loading. Tuning advice: start compaction prompts by maximizing recall, then iterate for precision.
**Transfers:** this is effectively our plugin's requirements document from the model vendor: tool-result clearing first, ledger notes second, lossy summary last, sub-agents for bulk reads. Follow its keep/discard list verbatim in our compaction prompt.

### Anthropic platform — context editing + memory tool (Sep 2025) and compaction API (Jan 2026) [verified]
`context-management-2025-06-27` beta: server-side **context editing** clears stale tool calls/results when nearing token limits, preserving conversation flow and (crucially) doing so cache-consciously. **Memory tool**: client-side file CRUD in a memory directory that persists across conversations. Reported: context editing alone +29% on agentic search evals, +39% combined with memory tool, **84% token reduction** on a 100-turn eval that otherwise fails. Jan 2026 added `compact_20260112`: server-side summarization when input tokens cross a configurable trigger, with steerable "preserve X" instructions and `pause_after_compaction` [verified via docs/search]. OpenAI shipped the same shape: automatic in-stream server compaction plus an explicit `/responses/compact` endpoint returning an encrypted compaction item [verified]; internal runs reported >25h / ~13M tokens on coding tasks [likely]. Google ADK ships sliding-window compaction (`compaction_interval`, `overlap_size`, pluggable `LlmEventSummarizer`) [verified].
**Transfers:** the API layer is racing to automate exactly the mechanical part. Our plugin should own the **judgement + ledger** layer (what matters *in this repo, this task*), and treat vendor compaction as the eventual substrate. The 29-39-84% numbers are the strongest quantitative case that tool-result clearing is where most of the win is.

### Claude Code native mechanics (docs, 2026) [verified — code.claude.com/docs/en/context-window]
What the plugin must build on: auto-compact runs "as you approach the limit" and is user-tunable via `/autocompact <tokens>`; `/compact` accepts focus instructions ("the summary keeps what you choose instead of what the automatic pass guesses"). **What survives compaction:** system prompt/output style (untouched); project-root CLAUDE.md, unscoped rules, and auto memory (re-injected from disk); path-scoped rules and nested CLAUDE.md (LOST until a matching file is re-read); invoked skill bodies (re-injected, capped 5k/skill and 25k total, oldest dropped); skill *descriptions* not re-listed. **Auto memory** (MEMORY.md): Claude's own cross-session notes, first 200 lines / 25KB auto-loaded. Compact summary keeps: user requests/intent, key technical concepts, files examined/modified with important snippets, errors and fixes, pending tasks, current work — verbatim tool outputs and intermediate reasoning are gone. `/context` gives a live per-category breakdown. Community deep-dives [likely — claudelog, hyperdev.matsuoka, oldeucryptoboi] describe a layered pipeline (drop whole messages → "microcompact" masking of recomputable tool results → LLM summarization), an auto-compact buffer of ~13k reserved tokens, and the VS Code extension triggering at ~75% usage with ~20% reserved for the compaction pass itself. A long-standing gap: PreCompact hooks receive **empty custom_instructions on auto-trigger** (GitHub issue #14160), so auto-compact quality can't currently be steered programmatically — a hole our plugin can fill by compacting *before* auto-compact fires.
**Transfers:** design constraints, not options: (a) put must-survive state in project-root CLAUDE.md / MEMORY.md / a ledger file, never in path-scoped rules or conversation; (b) trigger our own judged compaction below the auto-compact threshold so the native lossy pass rarely fires; (c) use `/context`-style accounting to inform judgement.

### Manus — "Context Engineering for AI Agents: Lessons from Building Manus" (Jul 2025) [verified via mirrors; original temporarily down]
Six lessons, all mechanism-level: (1) **Design around the KV-cache** — stable prompt prefix, append-only context, deterministic serialization; single-token prefix changes invalidate cache; ~**10x cost difference** cached vs uncached on Claude Sonnet. (2) **Mask, don't remove** — constrain tool availability by logit masking, never by editing tool definitions mid-episode (cache + confusion). (3) **Filesystem as ultimate context** — unlimited, persistent, agent-operated external memory; compression must be **restorable** (drop a web page's content but keep the URL; drop file contents but keep the path). (4) **Recitation** — the agent rewrites todo.md and appends it, pushing the global plan into the model's recent attention span, countering lost-in-the-middle on ~50-tool-call tasks. (5) **Keep the wrong stuff in** — failed actions and stack traces left in context measurably reduce repeat mistakes; "error recovery is one of the clearest indicators of true agentic behavior." (6) **Don't get few-shotted** — uniform action-observation history breeds drift/overimitation; inject controlled variation in serialization.
**Transfers:** the single most actionable industry source. Restorable-compression is our pruning rule (replace tool results with path+pointer stubs); recitation is our ledger's second function; keep-errors-in tempers over-aggressive pruning (never prune the *lesson* of a failure — distill it to the ledger first); append-only + prune-at-breakpoints protects cache economics.

### Cognition — "Don't Build Multi-Agents" (Jun 2025) [verified]
Principles: share full traces, not summaries ("actions carry implicit decisions, and conflicting decisions carry bad results"); default to a single-threaded linear agent; for tasks that outgrow the window, use a **dedicated compressor LLM** that "compress[es] a history of actions & conversation into key details, events, and decisions" (Devin does this internally); subagents only for well-defined read-only questions.
**Transfers:** validates single-agent + in-thread compression as the architecture (vs. multi-agent memory sharing); a compaction step is a *decision-preserving* transformation — our summary schema should be organized around decisions and events, not narrative.

### HumanLayer — 12-Factor Agents (2025) [verified repo; some claims secondary]
Factor 3 "Own your context window" (custom serialization, explicit token budgeting/pruning); Factor 9 "Compact errors into context window" (condense error info, keep diagnostic signal); Factor 10 small focused agents; Factor 12 stateless reducer. A secondary claim attributed to analysis of 100k developer sessions: a "dumb zone" in the middle 40-60% of large windows [unverified].
**Transfers:** factor 9 gives the error-handling rule: after an error is *resolved*, compact it to one line (error → cause → fix); while unresolved, keep it verbatim (agrees with Manus).

### LangChain — write/select/compress/isolate (2025) [verified]
The standard framing: **write** context out (scratchpads, memories), **select** it back in (retrieval), **compress** what's in the window (summarize/trim), **isolate** (split across sub-agents/sandboxes). LangGraph/LangMem implement checkpointing + memory types.
**Transfers:** a clean vocabulary for the plugin's four subsystems; our plan is write (ledger) + compress (prune/summarize) + isolate (subagent routing), with select = re-read from repo files.

### OpenHands — context condenser (2025) [verified]
`LLMSummarizingCondenser` (default): when history exceeds a size limit, keep recent events intact, summarize older ones into a `CondensationEvent` stored in the event log (original events retained out-of-band; summaries applied when building the LLM view). Reported up to **2x API-cost reduction with no performance degradation** on SWE-bench-style work; summaries encode goals, progress, remaining work, critical files, failing tests.
**Transfers:** the event-log-plus-view pattern is the right data model: never destroy the transcript; compaction only changes the *rendered* context. Their preserved-fields list matches Anthropic's — treat that intersection as the canonical summary schema.

### SWE-agent (2024) [verified]
`last_n_observations` (default 5): older tool observations collapsed to a single line each, keeping thoughts/actions. Crude but sufficient for 50-turn trajectories; fails when early observations still constrain later work — hence judgement, not FIFO.
**Transfers:** evidence that aggressive observation elision is survivable in coding tasks *if* the agent's own reasoning summaries remain; our pruning should prefer old observations whose content was already acted upon.

### Coding-tool memory features (2025-2026) [likely]
Windsurf Cascade auto-generates memories (~/.codeium/windsurf/memories); Cursor uses .cursor/rules + community "Memory Bank" file hierarchies; OpenAI Codex CLI reads AGENTS.md; a cottage industry of cross-agent memory layers (agentmemory, memories.sh) stores markdown/SQLite summaries and injects at session start.
**Transfers:** cross-session memory-in-files is commoditized; our differentiation must be *in-session* context management, which these do not attempt.

---

## Academic systems

### MemGPT / Letta (2023→) [verified]
The OS metaphor: fixed "main context" (system + working + FIFO queue) and unbounded "external context" (recall + archival storage), with the LLM itself issuing paging function calls and interrupts to move data — **self-editing memory via tool use**. Letta productized this (memory blocks, agent files).
**Transfers:** the founding statement of "the agent manages its own memory with tools" — exactly our plugin's thesis, but MemGPT pages *data*; we page *transcript state*. Its warning: memory-management calls compete with task attention, so make operations few and chunky.

### Sleep-time compute (Letta/Berkeley, Apr 2025) [verified]
Process/consolidate memory *between* interactions: pre-compute inferences about the persistent context while idle; ~**5x less test-time compute** for equal accuracy, ~2.5x cheaper amortized over related queries.
**Transfers:** a post-session (or post-milestone) consolidation pass — e.g., a SessionEnd/Stop hook distilling the transcript into MEMORY.md/ledger updates — buys next-session speed with zero in-session cost.

### Mem0 (Apr 2025) [verified]
Pipeline memory: extraction phase (salient facts from rolling window) + update phase (LLM chooses ADD/UPDATE/DELETE/NOOP against similar existing memories); optional graph variant. LOCOMO: +26% over OpenAI's memory, **91% lower p95 latency, >90% token savings** vs full-context.
**Transfers:** the ADD/UPDATE/DELETE/NOOP conflict-resolution step is the right discipline for ledger/MEMORY.md updates (dedupe, supersede, don't append forever). Its domain is chat personalization, not code — pipeline, not product, transfers.

### Zep / Graphiti (Jan 2025) [verified]
Temporal knowledge graph memory: bi-temporal model (event time vs ingestion time), every edge carries validity intervals (t_valid, t_invalid); non-lossy updates — facts are invalidated, not deleted. DMR 94.8%; LongMemEval +18.5% with 90% latency reduction.
**Transfers:** mostly overkill for one repo, but the **invalidate-don't-delete** principle maps directly: when a ledger fact goes stale ("bug X open" → fixed), mark superseded rather than silently dropping — prevents zombie facts after compaction.

### A-MEM (Feb 2025, NeurIPS 2025) [verified]
Zettelkasten-style agentic memory: each note gets structured attributes (context, keywords, tags, embedding); an LLM decides link creation, and new notes can trigger **memory evolution** — updating older notes' attributes.
**Transfers:** evidence that letting the agent *restructure* old notes (not just append) improves retrieval; supports a periodic "tidy the ledger" operation.

### HippoRAG (2024) [verified]
Hippocampal-index metaphor: LLM-extracted open KG as an index over passages + Personalized PageRank for single-step multi-hop retrieval.
**Transfers:** little directly — a repo already has an explicit index (the filesystem, grep, git). Reinforces Anthropic's point that agentic grep-retrieval over files beats building an embedding index for our use case.

### Generative Agents (Park et al., 2023) [verified]
Memory stream of timestamped observations; retrieval scored by **recency x importance x relevance**; **reflection** periodically synthesizes higher-level inferences from accumulated memories when an importance-sum threshold trips.
**Transfers:** two keepers: the three-factor scoring is a sane heuristic for *what to prune first* (old, low-importance, off-task tool results), and threshold-triggered reflection = "when enough has happened, distill before you forget" — a natural compaction trigger in addition to token pressure.

### Reflexion (Shinn et al., 2023) [verified]
Verbal self-reflection on failure stored in an episodic buffer and prepended to the next attempt; GPT-4 HumanEval 80%→**91%**.
**Transfers:** failure-driven memory writes: when a test fails or an approach is abandoned, that's precisely the moment to write a one-line lesson to the ledger — the transcript form of Reflexion, and the safe precondition for pruning the failure's raw logs.

### Voyager (Wang et al., 2023) [verified]
Skill library = **procedural memory**: every verified program stored as executable code indexed by NL description, retrieved and composed for new tasks; drove 15.3x faster tech-tree milestones vs prior SOTA, and ablating the library collapsed performance.
**Transfers:** Claude Code skills *are* the skill library. The plugin's cross-session arm should promote recurring verified procedures (build/test/deploy incantations) into skills or CLAUDE.md rather than re-deriving them; store *verified* procedures only.

### ACE — Agentic Context Engineering (Stanford/SambaNova, Oct 2025) [verified]
Contexts as **evolving playbooks** maintained by Generator/Reflector/Curator roles. Names two failure modes we must design against: **brevity bias** (summarization drops domain detail) and **context collapse** (iterative full rewrites erode information). Fix: structured, itemized, **incremental delta updates**. +10.6% AppWorld (matching a GPT-4.1 production agent with DeepSeek-V3.1), ~86.9% adaptation-latency reduction.
**Transfers:** ledger update discipline: item-structured file, delta merges, never wholesale regeneration. Brevity bias is the named risk of every /compact — our judged summaries should be *itemized*, not prose.

### The compaction line: ReSum, Context-Folding, AgentFold, ACON (Sep-Oct 2025) [verified]
- **ReSum** (Tongyi): periodically compress interaction history into a structured reasoning state and *resume from it*; +4.5% over ReAct (+8.2% with RL) on long-horizon search.
- **Context-Folding + FoldGRPO**: the agent **branches** into a sub-trajectory for a subtask, then **folds** it — collapsing intermediate steps into a concise outcome summary; matches/outperforms ReAct with an active context **10x smaller** and "significantly outperforms models that rely on summarization-based context management." AgentFold does multi-scale folding (granular condensation vs deep consolidation).
- **ACON**: optimizes *compression guidelines* in natural-language space via failure analysis (compare trajectories that succeeded with full context but failed with compressed → patch the guideline); 26-54% peak-token reduction, and compression *improved* smaller-agent success up to +46% by cutting distraction.
**Transfers:** the strongest academic endorsement of our core design: (a) fold at **subtask boundaries**, keeping outcome + constraints, instead of time/threshold-triggered blanket summarization; (b) maintain an explicit compaction *guideline* and refine it from observed failures (ACON's loop is exactly how we should iterate the plugin's compaction skill); (c) compaction is not only cost-saving — it can improve behavior by removing distraction.

### Memory condensation for coding agents (May 2026) [verified]
Eight condensation strategies x GPT-4o x 60 DiscoveryBench tasks: "**no condenser significantly alters hypothesis quality**"; LLM-based condensers **added 24-94% token cost**; **masking tool-call outputs achieved the only net saving (8.6%)**; optimal strategy varies by domain/length.
**Transfers:** the most decision-relevant empirical result for us: default to cheap deterministic masking/pruning of tool outputs; invoke LLM summarization sparingly and only when pruning is insufficient — else the compactor costs more than it saves.

### Harness-level systems: TokenPilot, ClawVM (2026) [verified abstracts]
- **TokenPilot**: names the core trade-off "text sparsity vs prompt-cache continuity" — pruning that mutates the sequence invalidates cached prefixes and can cost more than it saves. Dual-granularity: ingestion-aware compaction (stabilize prefixes, filter noise at entry) + lifecycle-aware eviction (offload segments only when their utility expires, batched by turn). 56-87% cost reductions.
- **ClawVM**: virtual-memory layer *in the harness*: typed pages, minimum-fidelity invariants (critical state always resident), multi-resolution representations under budget, validated writeback at lifecycle boundaries.
**Transfers:** TokenPilot's "evict on utility expiry, batched" is our pruning scheduler; ClawVM's "minimum-fidelity set" is our ledger formalized (the invariant: task intent + decisions + active files must always be in context or one Read away).

### Surveys / taxonomy papers [verified]
- **Memory in the Age of AI Agents** (Dec 2025): organizes by **forms** (token-level, parametric, latent) x **functions** (factual, experiential, working) x **dynamics** (formation, evolution, retrieval); argues long/short-term is no longer sufficient.
- **Rethinking Memory Mechanisms of Foundation Agents** (Feb 2026): five atomic systems — sensory, working, episodic, semantic, procedural.
- **CoALA** (2023): working memory as the hub; long-term = episodic/semantic/procedural; internal actions = retrieval, reasoning, learning (write to LTM).

---

## Degradation evidence (when to trigger compaction)

- **Chroma "Context Rot"** (Jul 2025) [verified]: 18 SOTA models; reliability declines with input length **even on trivially simple tasks** (retrieval, text replication); degradation is non-uniform and worsened by distractors, low needle-question similarity, and (counterintuitively) coherent haystack structure. Secondary sources put clearly observable degradation for 1M-window models around 300-400k tokens [likely]. Implication: *ambient* long context is never free; irrelevant tool logs are distractors, not neutral filler.
- **Lost in the Middle** (Liu et al., 2023) [verified]: U-shaped position curve; >30% accuracy drop when the key document moves from the edge to the middle of a 20-doc context. Implication: mid-context material (old ledger writes, early decisions) is functionally faded — recitation (re-append) restores it; this is *why* Manus's todo.md works.
- **NoLiMa** (Adobe, ICML 2025) [verified]: with literal lexical overlap removed, 11/13 models fall below **50% of their short-context baseline by 32k tokens**. Implication: the associative reasoning coding requires (connecting a symptom to a design decision made 80k tokens ago) degrades far earlier than needle benchmarks suggest — justifying compaction thresholds well below the window size.
- **LLMLingua / LongLLMLingua** (Microsoft) [verified]: token-level compression up to 20x with <2% loss; LongLLMLingua +17.1% accuracy at 4x compression — compression can *improve* performance by removing distraction. Same directional finding as ACON (+46% for small agents). We don't need their perplexity machinery, but the conclusion transfers: pruning is performance-positive, not just cost-positive.
- **Practical trigger points in production tools** [likely]: Claude Code reserves ~13k buffer and (VS Code) compacts at ~75% usage with ~20-25% reserved for the pass itself; HumanLayer-adjacent analyses claim a mid-window "dumb zone" beyond ~40% fill [unverified]. Synthesis: **judged compaction should begin around 40-60% utilization at natural boundaries, and must complete before ~75-80%** where native auto-compact takes over and quality/latency pressure spikes.

---

## Taxonomy

Working mapping for a CLI coding agent:

| Cognitive type | Agent artifact (our setting) | Ops |
|---|---|---|
| Working memory | The live context window: recent turns, active file excerpts, current plan | prune, recite, compact (LangChain: compress) |
| Episodic | Transcript + session summaries; "what we tried, what failed" | Reflexion-style lessons; fold subtask trajectories into outcomes |
| Semantic | Repo facts: architecture, invariants, decisions + rationale | ledger file, CLAUDE.md, MEMORY.md; invalidate-don't-delete (Zep) |
| Procedural | Verified how-tos: build/test/run incantations, workflows | skills, CLAUDE.md rules (Voyager pattern) |

Cross-cutting framings: LangChain **write/select/compress/isolate** (operations); Anthropic's retrieval stance (just-in-time, filesystem-as-index); "Memory in the Age of AI Agents" forms x functions x dynamics (use *dynamics* — formation/evolution/retrieval — as the checklist when designing each store).

---

## Ranked actionable patterns for our plugin

1. **Reversible pruning of stale tool results, with pointer stubs (highest value/cost ratio).** Replace consumed tool outputs (file reads since edited or already acted on, old test logs, search results) with one-line stubs: `[pruned: Read src/auth.ts (2,400 tok) — re-read if needed]`. Evidence: Anthropic context editing (+29%, 84% token savings), Manus restorable compression, SWE-agent n=5 elision, coding-agent condensation study (masking = only net-positive strategy), microcompact. Deterministic, no LLM cost, loss-free by construction. Prune the *content*, never the fact that the action happened (avoids re-doing work and preserves the error-history signal).
2. **A ledger file (task memory) written continuously and recited.** One markdown file per task (e.g. `.claude/ledger.md` or in-repo): intent, plan/todo with status, decisions + rationale, active files, unresolved errors, verified facts. Update as **itemized deltas** (ACE: avoid brevity bias and context collapse; Mem0: ADD/UPDATE/DELETE/NOOP; Zep: supersede, don't silently drop). Re-append (recite) after significant progress and immediately before any compaction — it is simultaneously attention steering (Manus, lost-in-the-middle) and the lossless checkpoint that makes /clear or auto-compact survivable (Anthropic note-taking; context-mode's PreCompact snapshot proves the mechanic in a Claude Code plugin).
3. **Fold at subtask boundaries, judged by the agent.** When a subtask completes (tests pass, bug fixed, exploration concluded), collapse its trajectory to outcome + constraints + lesson, and prune its raw steps. Evidence: Context-Folding (10x smaller active context, beats blanket summarization), AgentFold, Cognition's decision-preserving compression, Generative Agents' threshold-triggered reflection. This is the "judgement-driven" core: boundaries, not byte counts, decide *when*; byte counts only decide *urgency*.
4. **Trigger policy: early, staged, and before the native pass.** Begin judged cleanup ~40-60% utilization; ensure a full checkpoint exists before ~75-80% where auto-compact fires (whose auto-trigger currently can't be steered — GitHub #14160). Ground: context rot on simple tasks, NoLiMa's 32k associative cliff, Claude Code's reserved buffers. Escalation ladder mirroring Claude Code/Manus: (a) prune stubs → (b) fold subtasks → (c) recite ledger + /compact with focus instructions → (d) /clear + ledger re-read (lossless because of #2).
5. **A fixed, itemized summary schema for anything lossy.** Union of Anthropic/Claude Code/OpenHands/Devin lists: user intent; decisions + why; key technical concepts; files touched w/ critical snippets; errors seen + fixes (resolved errors compacted to one line — 12-factor #9; unresolved kept verbatim — Manus); pending tasks; current state; next step. Maximize recall first, tune precision later (Anthropic).
6. **KV-cache discipline.** Append-only in the hot loop; batch prunes at turn boundaries/breakpoints rather than continuous micro-edits (TokenPilot: sequence mutation can cost more than the tokens saved; Manus: ~10x cached-vs-uncached price). A prune that saves 5k tokens but invalidates a 100k-token cached prefix is a loss.
7. **Route bulk reads through subagents; keep returns ~1-2k tokens.** Native, already encouraged; the plugin's job is judgement nudges (e.g. flag when the main thread is about to slurp >N tokens of exploration).
8. **Failure-driven guideline refinement (ACON's loop) as our eval method.** Keep the compaction/pruning policy in a natural-language skill file; when a post-compaction mistake occurs (re-reading pruned content, re-doing work, contradicting a decision), patch the guideline. Cheap, and turns incidents into policy.
9. **Cross-session consolidation on Stop/SessionEnd (sleep-time pattern).** Distill transcript → MEMORY.md/ledger deltas; promote recurring verified procedures to skills (Voyager); write Reflexion-style lessons on failures. Amortized cost is near zero; next session boots warm.
10. **Don't build retrieval infrastructure.** No embeddings/KG for a single repo: filesystem + grep + git is the index (Anthropic just-in-time; HippoRAG's machinery is for corpus-scale association). SQLite FTS only if ledgers grow beyond directly readable size (claude-mem's choice).

---

## Prior art closest to our plan

- **claude-mem** (thedotmack; plugin) — nearest neighbor overall. Hooks (SessionStart/UserPromptSubmit/PostToolUse/Summary/SessionEnd) capture observations; agent-SDK worker compresses to a local SQLite (FTS5 + optional Chroma) store; injects last-10-session context at start. Beta "Endless Mode": compresses tool outputs into ~500-token observations, transforming the transcript in real time, working-memory vs archive split [likely — flagship docs page doesn't detail it]. **Difference from us:** harness-side and automatic (background worker decides), primarily cross-session; our plan is in-session, agent-judgement-driven, with the model choosing what/when to fold. Build on: their hook topology and local-store choices are proven; don't re-derive.
- **context-mode** (mksglu) — ingress prevention rather than cleanup: hooks force tool output through sandboxed `ctx_execute` (only stdout enters context; full output → per-project SQLite FTS5 KB, intent-filtered retrieval; 56KB → 299B examples); PreCompact builds a ≤2KB priority-tiered snapshot; SessionStart restores it. ~98% context saving with hooks enforced. **Difference:** deterministic routing policy, not judgement; alters the tool surface (agent must use ctx_* tools). Its PreCompact/SessionStart snapshot-restore pair is exactly our checkpoint mechanic, validated.
- **dxta dynamic-context-pruning / "context-manager"** [likely — page 403'd]: MCP tools + hooks for save/load structured checkpoints that survive compaction, duplicate-tool-call tracking, marking subtask completion for safe reduction — conceptually the closest *stated* feature list to ours; maturity unknown.
- **precompact-hook** (mvara-ai) and assorted handoff plugins (auto-snapshot to `.claude/handoff_current.md` on exit, reload on start; manual /handoff): the checkpoint half of our plan exists in several small forms.
- **Anthropic native trajectory**: context editing, memory tool, `compact_20260112` compaction (with steerable preservation), Claude Code auto-compact/microcompact + `/autocompact` + MEMORY.md auto memory. The mechanical layer is being absorbed into the platform — our plugin should be the **judgement + repo-specific ledger** layer that the platform explicitly does not provide (auto-compact's unsteerable trigger is the documented gap).
- **Academic near-duplicates**: Context-Folding/FoldGRPO and AgentFold are agent-driven self-compaction *trained into the model* — we implement the same policy via prompting/skills; ReSum's summary-conditioned resumption and ACON's guideline optimization are the method-level blueprints; MemGPT is the ancestral statement of self-managed memory-as-tools.

Net: no existing work combines (a) in-session, (b) judgement-driven (model decides what/when), (c) reversible-first pruning, and (d) a recited lossless ledger, inside a Claude Code plugin. Every component exists separately and is validated; the composition is the contribution.

---

## Sources

Industry:
- Anthropic, Effective context engineering for AI agents — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic/Claude, Managing context on the Claude Developer Platform — https://claude.com/blog/context-management
- Claude Platform docs: Memory tool — https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool ; Context editing — https://platform.claude.com/docs/en/build-with-claude/context-editing ; Compaction — https://platform.claude.com/docs/en/build-with-claude/compaction
- Claude Code docs, Explore the context window — https://code.claude.com/docs/en/context-window
- Claude Code auto-compact analyses — https://claudelog.com/faqs/what-is-claude-code-auto-compact/ ; https://hyperdev.matsuoka.com/p/how-claude-code-got-better-by-protecting ; https://oldeucryptoboi.com/blog/context-compaction-deep-dive/ (403 at review time)
- PreCompact custom-instructions gap — https://github.com/anthropics/claude-code/issues/14160
- Manus, Context Engineering for AI Agents — https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus (down for maintenance 2026-08-23; summarized via https://www.marktechpost.com/2025/07/22/context-engineering-for-ai-agents-key-lessons-from-manus/)
- Cognition, Don't Build Multi-Agents — https://cognition.com/blog/dont-build-multi-agents
- HumanLayer, 12-Factor Agents — https://github.com/humanlayer/12-factor-agents
- LangChain, Context Engineering for Agents — https://www.langchain.com/blog/context-engineering-for-agents
- OpenHands condenser — https://www.openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents ; https://docs.openhands.dev/sdk/guides/context-condenser
- OpenAI compaction — https://developers.openai.com/api/docs/guides/compaction ; https://developers.openai.com/blog/skills-shell-tips
- Google ADK context compaction — https://google.github.io/adk-docs/context/compaction/
- SWE-agent — https://arxiv.org/abs/2405.15793

Academic:
- MemGPT — https://arxiv.org/abs/2310.08560
- Sleep-time Compute — https://arxiv.org/abs/2504.13171 ; https://www.letta.com/blog/sleep-time-compute/
- Mem0 — https://arxiv.org/abs/2504.19413
- Zep/Graphiti — https://arxiv.org/abs/2501.13956
- A-MEM — https://arxiv.org/abs/2502.12110
- HippoRAG — https://arxiv.org/abs/2405.14831
- Generative Agents — https://arxiv.org/abs/2304.03442
- Reflexion — https://arxiv.org/abs/2303.11366
- Voyager — https://arxiv.org/abs/2305.16291
- ACE (Agentic Context Engineering) — https://arxiv.org/abs/2510.04618
- ReSum — https://arxiv.org/abs/2509.13313
- Context-Folding — https://arxiv.org/abs/2510.11967 ; AgentFold — https://arxiv.org/abs/2510.24699 ; FoldAct — https://arxiv.org/abs/2512.22733
- ACON — https://arxiv.org/abs/2510.00615
- Memory condensation for coding agents — https://arxiv.org/abs/2605.18854
- TokenPilot — https://arxiv.org/abs/2606.17016 ; ClawVM — https://arxiv.org/abs/2604.10352
- OPENDEV terminal coding agents — https://arxiv.org/abs/2603.05344
- CoALA — https://arxiv.org/abs/2309.02427
- Memory in the Age of AI Agents (survey) — https://arxiv.org/abs/2512.13564 ; Rethinking Memory Mechanisms (survey) — https://arxiv.org/abs/2602.06052

Degradation:
- Chroma, Context Rot — https://research.trychroma.com/context-rot
- Lost in the Middle — https://arxiv.org/abs/2307.03172
- NoLiMa — https://arxiv.org/abs/2502.05167
- LLMLingua — https://www.microsoft.com/en-us/research/project/llmlingua/ ; https://github.com/microsoft/LLMLingua

Prior-art plugins:
- claude-mem — https://docs.claude-mem.ai/introduction ; https://github.com/thedotmack/claude-mem
- context-mode — https://github.com/mksglu/context-mode
- precompact-hook — https://github.com/mvara-ai/precompact-hook
- dxta dynamic-context-pruning — https://lobehub.com/mcp/dxta-claude-dynamic-context-pruning (403 at review time)
