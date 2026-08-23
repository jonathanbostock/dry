# Community Practice: Folk Wisdom on Context Management in Claude Code

Research pass conducted 2026-08-23. Ethnographic survey of what heavy Claude Code users actually
do about context in long sessions — rituals, thresholds, vocabulary, and shared hacks — as
distinct from official documentation. Sources are web search + fetch (WebSearch/WebFetch tools);
direct Reddit fetches were blocked (`Claude Code is unable to fetch from www.reddit.com`) so
Reddit content here is second-hand via search-engine summaries/quotes, flagged as such. Hacker
News was reachable via the Algolia API. All dates/model names below are as reported by sources
current to Aug 2026; treat specific token-threshold numbers as informal folklore, not benchmarked
fact.

---

## Summary

There is a real, fairly consistent folk consensus, independently arrived at by Anthropic staff,
third-party "agent whisperers," and rank-and-file Reddit/HN users: **treat the context window like
a scarce, decaying resource, not a bottomless log.** The consensus core is (1) compact or clear
*before* quality visibly drops, not after — with "60% not 95%" repeated almost verbatim by
multiple independent sources; (2) never let auto-compact fire mid-task, because it summarizes at
the point where the model is already degraded and can hallucinate actions (e.g. auto-committing
work nobody asked for); (3) externalize state to disk (`PLAN.md`/`plan.md`/`NOTES.md`/CLAUDE.md)
so that clearing/compacting is lossless in effect even though the transcript is gone; (4) use
subagents as "context firewalls" to keep read-heavy/noisy work out of the main thread; and (5)
keep CLAUDE.md short and curated, because a bloated one causes Claude to ignore instructions
buried in the noise — the same "context rot" mechanism that degrades long sessions, self-inflicted.
Anthropic's own docs (`best-practices`, `effective-context-engineering-for-ai-agents`) have by now
absorbed most of this folklore almost verbatim (e.g. "kitchen sink session," "the over-specified
CLAUDE.md"), which suggests the practitioner community and the vendor converged rather than one
teaching the other. The genuinely load-bearing outside-the-docs bits are: Dex Horthy's "dumb zone"
(middle 40–60% of the window is where recall/reasoning quietly falls apart), Drew Breunig's
four-part failure taxonomy (poisoning/distraction/confusion/clash), Geoffrey Huntley's Ralph
Wiggum loop (fresh context every iteration as a *design principle*, not a fallback), and a small
ecosystem of statusline/hook gists that expose context-% and snapshot state pre-compaction because
Claude Code doesn't do either natively as well as users want.

---

## Rituals & thresholds

### `/clear` vs `/compact` — the basic split

The community's rule of thumb, repeated across blogs and distilled into Anthropic's own docs:

> "If you're wrapping up one piece of work and moving on to something different, clear is the
> right call... If you're in the middle of a longer task and need to shed some token weight
> without losing your thread, compact earns its place." — paraphrased community consensus,
> [Blink Blog / Medium summaries](https://medium.com/@nustianrwp/managing-your-context-window-clear-vs-compact-in-claude-code-8b00ae2ed91b)

Anthropic's own `best-practices` doc now codifies exactly this as a named anti-pattern:

> "**The kitchen sink session.** You start with one task, then ask Claude something unrelated,
> then go back to the first task. Context is full of irrelevant information. **Fix**: `/clear`
> between unrelated tasks." — [code.claude.com/docs/en/best-practices](https://code.claude.com/docs/en/best-practices)

> "**Correcting over and over.** Claude does something wrong, you correct it, it's still wrong,
> you correct again... **Fix**: After two failed corrections, `/clear` and start fresh with a more
> specific prompt that incorporates what you learned. A clean session with a better prompt almost
> always outperforms a long session with accumulated corrections." — same source

This "two strikes and you clear" rule shows up independently as informal practitioner wisdom
before it was codified — the two-correction threshold is specific enough that it reads like it was
lifted from user behavior rather than invented in-house.

### The proactive-compaction threshold ("60% not 95%")

The single most repeated, most load-bearing number in this research:

> "Running /compact before you hit the limit is genuinely one of those things nobody tells you.
> The auto-compact fires when the model is already struggling. Manual compact at 600K means you
> get a clean summary while the model can still think." — attributed to r/ClaudeAI community
> discussion, via [MindStudio summary](https://www.mindstudio.ai/blog/claude-code-compact-command-context-management-2)

> "Running /compact at 60% context capacity — not 95% — keeps your Claude Code sessions sharp." —
> same thread summary

Thariq Shihipar (Anthropic, Claude Code team) gives the equivalent advice as an actual lever, on X:

> "You can also set your autocompact threshold yourself and effectively lower your context window
> if you'd prefer. For example, 400k context is a good compromise:
> `CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000`" —
> [@trq212 on X](https://x.com/trq212/status/2044653085415604473)

Independent practitioner Albert Sikkema goes further and disables the 1M window entirely,
citing both Aider's Paul Gauthier ("models get confused when you feed them more than ~25–30k
tokens") and the NoLiMa benchmark ("11 of 12 tested models dropped below 50% of their
short-context performance at just 32k tokens"):

> Config used: `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` —
> triggers compaction around 140k tokens instead of the default ~950k threshold. —
> [Why I Shrunk Claude Code's Context Window Back to 200k](https://www.albertsikkema.com/ai/development/tools/2026/04/23/smaller-context-window-better-claude-code.html)

His trigger anecdote: Claude auto-committed work mid-task after an auto-compaction, having lost
the fact that a commit had never been requested — a concrete, oft-repeated failure story (also
reported independently by other users per search summaries) that functions as the community's
canonical cautionary tale for "never let auto-compact fire mid-task."

### "Never let auto-compact fire mid-task"

Practically universal advice, phrased slightly differently by everyone:

> "When Claude Code compacts the session automatically near the context limit, if that happens
> mid-task, check the current plan, modified files, and last test results before continuing,
> because subtle earlier decisions may now exist only as summary." — practitioner synthesis via
> search, echoing the Sikkema/community anecdotes above.

Anthropic's docs mirror this by making manual, scoped compaction the recommended default and
auto-compact the fallback (see `/compact <instructions>`, `/rewind` → "Summarize from/up to here").

### Compact-with-custom-instructions recipes

Both community and vendor material converge on templated compact instructions:

> `/compact "Keep the API design decisions and the database schema, summarize the debugging
> session"` — widely circulated example, e.g.
> [Blink Blog](https://blink.new/blog/claude-code-context-management)

> "Customize compaction behavior in CLAUDE.md with instructions like `'When compacting, always
> preserve the full list of modified files and any test commands'` to ensure critical context
> survives summarization." — [Anthropic best-practices doc](https://code.claude.com/docs/en/best-practices)

> `/compact Focus on the API changes` — Anthropic's own minimal example, same doc.

### Plan-file workflows (PLAN.md / TODO.md / PROGRESS.md / scratchpad dirs)

This is the single biggest structural pattern across every practitioner community: **externalize
state to a file so that clearing the transcript doesn't lose the work.**

- Kieran Klaassen / Every's "Compound Engineering" system: a `plan.md` is "external memory that is
  stored on the file system and does not disappear when the session ends, allowing each new Agent
  session to reload the context from this file instead of relying on decayed chat history." —
  [creatoreconomy.so summary of Kieran Klaassen](https://creatoreconomy.so/p/how-to-make-claude-code-better-every-time-kieran-klaassen);
  canonical loop is **Plan → Work → Assess → Compound**, "roughly 80% of effort in planning and
  review and 20% in execution." Compound step: *"Capture learnings into docs that Claude reads
  next time. Simplest version: 'add this to claude.md.'"*
- Anthropic's own "let Claude interview you" workflow ends with: "write a complete spec to
  SPEC.md... Once the spec is complete, start a fresh session to execute it. The new session has
  clean context focused entirely on implementation, and you have a written spec to reference." —
  [best-practices doc](https://code.claude.com/docs/en/best-practices)
- Scratchpad convention: `.claude/.scratch/` with `drafts/`, `experiments/`, `notes/`, `temp/`
  subdirectories, and a `CLAUDE_CODE_TMPDIR` env var so teams can redirect it off ephemeral `/tmp`
  in containers — [qubytes.substack.com](https://qubytes.substack.com/p/scratchpad-claude-code-context-window-task-state),
  [Piebald-AI/claude-code-system-prompts](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/system-prompt-scratchpad-directory.md)
- Simon Willison, via Drew Breunig's "context offloading" concept, calls out "agents maintaining
  `plan.md` files during problem-solving" as a named technique —
  [How to Fix Your Context](https://simonwillison.net/2025/Jun/29/how-to-fix-your-context/)
- Greg Ceccarelli's "goal engineering": a checked-in, timestamped pair of docs per unit of work —
  a ≤4,000-character `goal.md` ("the cap forces decisions") and an unbounded `rider.md` ("the rider
  forces precision") living in `docs/goals/`, explicitly designed to be `grep`-able weeks later —
  [gregceccarelli.com/goal-engineering](https://www.gregceccarelli.com/goal-engineering)

### Recitation (re-reading the plan to re-anchor attention)

Not Claude-Code-specific in origin (this pattern is documented in Manus's and Google's agent
writeups) but widely imported into Claude Code plan-file workflows:

> "Agents re-read and update a todo.md file at every step before taking an action, which recites
> the global goal into the end of the context window — exactly where attention is strongest... By
> constantly rewriting the todo list, agents recite their objectives into the end of the context,
> pushing the global plan into the model's recent attention span, avoiding 'lost-in-the-middle'
> issues and reducing goal misalignment." — synthesis via search of the Manus context-engineering
> writeup and follow-on commentary;
> see [Manus: Context Engineering for AI Agents](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)

Claude Code's own built-in TODO-list tool (the checklist shown during sessions) is the productized
version of this same recitation principle — Anthropic's context-engineering post specifically
cites Claude's to-do list and a `NOTES.md`-style pattern as "structured note-taking."

### Handoff documents / "leave a note for the next Claude"

A whole cottage industry of "handoff prompt" templates exists. Common shape, synthesized across
several near-identical templates:

> "A handoff prompt should include your goal, current status, key decisions, what to avoid, and
> the very next step in a short, clear structure so the next AI can continue immediately and
> efficiently." — [jdhodges.com](https://www.jdhodges.com/blog/ai-session-handoffs-keep-context-across-conversations/),
> similar templates at [getclaudeos.com](https://getclaudeos.com/tools/session-handoff) and
> [claudecodehq.com](https://www.claudecodehq.com/playbooks/session-capture-handoff)

Quick-and-dirty version people actually type: *"Before we end and start a new session, write a
complete handoff prompt I can paste into a new chat with any AI assistant."*

A close variant, described as a practitioner tip on maintaining context across long sessions: **end
every meaningful session with a "State of the Union" summary** — what was achieved, current state
of the codebase, exact next steps, known bugs/edge cases — pasted at the start of the next session.
(Via search synthesis of long-session-management blog posts; exact original author unclear —
treat as [unverified] provenance but a real, commonly-cited pattern.)

The `pre-compact.js` hook in the `everything-claude-code` repo automates this exact ritual: it
fires right before compaction, generates an LLM summary of the session, and writes it between
`<!-- ECC:SUMMARY:START -->` / `END` markers in a session file, specifically "to capture a
high-quality summary even after lossy compaction" — i.e., leaving a note for the next Claude
automatically rather than relying on the user to ask for one. —
[affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code/blob/main/scripts/hooks/pre-compact.js)

### One-task-one-session discipline

> "One task, one session. Don't chain." "The issue is that every 5 new tasks chained together can
> result in 1 regression from context drift. One long session costs more than the same work spread
> over a few short ones, and by more than you'd think, because turn 40 is also re-reading the 39
> turns before it." — practitioner synthesis via search (source blog unclear, but the framing —
> especially the "turn 40 re-reads turn 39" mechanism — recurs verbatim-ish enough across sources
> to be treated as a real community meme, [likely] traceable to a specific Substack/DEV post not
> independently confirmed here)

> "Each Claude Code session should map to exactly one task -- mixing tasks in a single session
> degrades context quality... One worktree per task, one session per worktree." — same cluster of
> sources.

This is also just Anthropic's "kitchen sink session" anti-pattern from the other direction.

### Subagents as context firewalls

The "firewall" framing is now completely mainstream vocabulary, echoed by Anthropic itself:

> "Since context is your fundamental constraint, subagents are one of the most powerful tools
> available. When Claude researches a codebase it reads lots of files, all of which consume your
> context. Subagents run in separate context windows and report back summaries." —
> [Anthropic best-practices](https://code.claude.com/docs/en/best-practices)

> "Subagents are valuable because they're a firewall that keeps tokens of test output out of your
> main thread... When a subagent reads 30 files to do a code review, those 30 file reads happen in
> its context, not yours." — practitioner synthesis,
> [Tembo.io guide](https://www.tembo.io/blog/claude-code-subagents) /
> [MindStudio](https://www.mindstudio.ai/blog/sub-agents-claude-code-context-management)

Armin Ronacher's variant: use a *different tool entirely* as the firewall — "While running Claude
Code, use Gemini CLI to run sub-agents, to perform additional tasks without using up Claude Code's
own context." — synthesis of
[lucumr.pocoo.org/2025/6/12/agentic-coding](https://lucumr.pocoo.org/2025/6/12/agentic-coding/)

Anthropic's docs also formalize the **Writer/Reviewer pattern** as a context-hygiene technique in
its own right (fresh reviewer session isn't biased toward code it just wrote) and the **adversarial
review subagent** ("have a subagent review the diff in a fresh context and report gaps... Tell the
reviewer to flag only gaps that affect correctness... treat the rest as optional" — to avoid
over-engineering from a reviewer incentivized to find *something*).

### Redirecting big command output to files instead of context

> "Add quiet flags to noisy commands, or run them in a subagent. Command output is added to the
> conversation just like a file, and stays there for the rest of the session." Large outputs
> (30,000+ characters) are auto-written to files with previews shown inline. —
> [claude.com/blog/maximizing-the-value-of-your-claude-code-sessions](https://claude.com/blog/maximizing-the-value-of-your-claude-code-sessions)

Peter Steinberger's harder-line version, from his running critique of MCP: MCP servers "clutter up
context," "ate 40% of my context window, crashed randomly," and his conclusion — "mcp were a
mistake. bash is better" — because a CLI + `jq` piping lets the *agent* choose what enters context
rather than the tool dumping everything in: "not a problem when you use mcporter to call your
mcps. Plus, your agent can filter output with jq so uses far less tokens." —
[@steipete on X (1)](https://x.com/steipete/status/1921838640855994616),
[(2)](https://x.com/steipete/status/1989853716921434227)

### Git-commit-as-checkpoint + `/rewind` usage

> "Git is long-term version management, /rewind is instant undo within a session. They complement
> each other. Best practice: Commit at every important milestone, then use `/rewind` for
> fine-grained rollbacks between commits." — community synthesis via
> [MindStudio](https://www.mindstudio.ai/blog/claude-code-rewind-command-rollback) and
> [buttondown.com/redpen](https://buttondown.com/redpen/archive/checkpoints-and-rewind-undoing-a-claude-code/)

Anthropic's docs add the specific reason to prefer `/rewind` over `/compact` for "that went wrong,
undo it" scenarios: **rewinding is free because it doesn't touch the prompt cache**, whereas
re-prompting after a bad turn burns cache. `/rewind` also now supports partial summarization
("Summarize from here" / "Summarize up to here") — i.e., compact only part of a conversation,
keeping either the tail or the head verbatim. Important limitation repeatedly flagged: checkpoints
only capture Claude's own file-edit tool calls, **not** Bash-driven changes or edits made outside
Claude Code — so checkpoints are explicitly *not* a git replacement.

### `/catchup` / re-onboarding after clears

No dedicated `/catchup` command was found (contrary to the brief's hypothesis) — the actual
primitives are `claude --continue` (resume most recent session for cwd, zero config) and
`claude --resume` / `/resume` (picker across saved sessions, full history + tool results intact).
Practitioner-added value on top of these: **naming sessions** (`/rename`) so they function like
git branches — "treat them like branches: each workstream gets its own persistent context." —
[Anthropic best-practices](https://code.claude.com/docs/en/best-practices). Simon Willison
personally runs this pattern hard: he reports keeping "11 concurrent inactive sessions," resuming
individual ones "after hours or days" rather than re-deriving context each time —
[Recent Claude Code Quality Reports](https://simonwillison.net/2026/Apr/24/recent-claude-code-quality-reports/).

### CLAUDE.md curation discipline

Extremely convergent advice, essentially identical whether the source is Anthropic, Boris Cherny,
or random blogs:

> "Keep it concise. For each line, ask: 'Would removing this cause Claude to make mistakes?' If
> not, cut it. Bloated CLAUDE.md files cause Claude to ignore your actual instructions!" —
> [Anthropic best-practices](https://code.claude.com/docs/en/best-practices)

> "Adding more rules to your CLAUDE.md can make Claude follow fewer of them. That is the
> counterintuitive heart of the CLAUDE.md best practices that work." — community synthesis,
> [DEV Community summary](https://dev.to/nishilbhave/claudemd-best-practices-the-complete-2026-guide-435j)

> "If Claude keeps skipping one instruction, add emphasis such as 'IMPORTANT' to that line alone.
> If you emphasize many lines, none of them stands out." — Anthropic best-practices doc

Boris Cherny's team-level ritual: "Anytime we see Claude do something incorrectly we add it to the
CLAUDE.md, so Claude knows not to do it next time," and he uses `@.claude`-tags on coworkers' PRs
to route learnings into the file — i.e. CLAUDE.md as a team's living postmortem log, checked into
git. — [howborisusesclaudecode.com](https://howborisusesclaudecode.com/),
[GitHub summary of his 13 tips](https://github.com/shanraisshan/claude-code-best-practice/blob/main/tips/claude-boris-13-tips-03-jan-26.md)

Ryan Spletzer's periodic-audit ritual, literally asking Claude to prune itself: *"Review my
`CLAUDE.md` and identify anything that could be removed, consolidated, or moved to a skill that
only loads when needed."* — [Shedding Dead Context](https://www.spletzer.com/2026/03/shedding-dead-context/)
(see Glossary: "dead context").

Shared length target across nearly every source: **roughly 200 lines / under ~200–300 lines**,
push anything conditional into Skills (loaded on demand) rather than CLAUDE.md (loaded every turn).

---

## Glossary

**Context rot** — "As the number of tokens in the context window increases, the model's ability to
accurately recall information from that context decreases," rooted in the transformer's O(n²)
pairwise attention. Popularized as a named, benchmarked phenomenon by Chroma's July 2025 research
report (18 models tested, GPT-4.1/Claude 4/Gemini 2.5/Qwen3), which found degradation is
non-uniform, accelerates with low needle-question similarity, and — counterintuitively — gets
*worse* on logically-structured ("coherent") long contexts than shuffled ones. —
[Chroma: Context Rot](https://www.trychroma.com/research/context-rot) (260 pts / 59 comments on HN)

**Context poisoning** — "A hallucination or other error makes it into the context, where it is
repeatedly referenced" and compounds because the agent treats its own past output as ground truth.
Canonical example: Google DeepMind's Claude/Gemini-plays-Pokémon agent got its internal
goals/summary "poisoned" with wrong game-state info that took a very long time to undo. Coined/
popularized by **Drew Breunig**, "How Long Contexts Fail" (dbreunig.com, 22 Jun 2025).

**Context distraction** — "As context balloons... the agent showed a tendency toward favoring
repeating actions from its vast history rather than synthesizing novel plans" (observed past
~100k tokens in the Pokémon case study) — i.e. the model over-indexes on transcript history over
its trained-in knowledge. Breunig, same essay.

**Context confusion** — "Superfluous information in the context is used by the model to generate a
low-quality response" — e.g. the Berkeley Function-Calling Leaderboard finding that "every model
performs worse when provided with more than one tool." Breunig, same essay.

**Context clash** — "New information acquired during multi-turn interactions directly conflicts
with earlier context," causing models to lock onto early wrong assumptions. Breunig, same essay.
Simon Willison amplified the whole taxonomy the same week in
[How to Fix Your Context](https://simonwillison.net/2025/Jun/29/how-to-fix-your-context/), adding
the fix-side vocabulary: **tool loadout** (models get confused past ~20 tools — trim what's
enabled), **context quarantine** (isolate contexts in dedicated threads/subagents), **context
pruning/summarization**, and **context offloading** (store info outside the window, e.g. a
`plan.md`). Willison's one-line thesis: **"context is not free."**

**Context engineering** — The umbrella term for all of the above. Precedent-setting use by Walden
Yan at Cognition (Devin) earlier in 2025; went viral after Shopify CEO Tobi Lütke's 19 Jun 2025 X
post ("the art of providing all the context for the task to be plausibly solvable by the LLM") and
Andrej Karpathy's 25 Jun 2025 follow-up ("context engineering is the delicate art and science of
filling the context window with just the right information for the next step"). Anthropic
canonized it with [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
(29 Sep 2025, alongside Sonnet 4.5), defining it as "curating and maintaining the optimal set of
tokens... during LLM inference" and framing the core skill as finding "the smallest possible set
of high-signal tokens that maximize the likelihood of some desired outcome." LangChain separately
popularized a four-verb operational framework — **write / select / compress / isolate** context —
worth stealing directly for a plugin's taxonomy:
[langchain.com/blog/context-engineering-for-agents](https://www.langchain.com/blog/context-engineering-for-agents).

**The "dumb zone"** — Dex Horthy (HumanLayer, 12-factor-agents): after analyzing "100,000 developer
sessions," the finding that "the middle 40–60% of a large context window" is where "model recall
degrades and reasoning falters," and — the sharper claim — **you cannot prompt your way out of it**
("the more you use the context window, the worse outcomes you get"). Practical rule he uses
himself: push a 1M-token model to ~300–400K max; stop smaller models around 100K. —
synthesis of [Pragmatic Engineer interview](https://newsletter.pragmaticengineer.com/p/context-engineering-with-dex-horthy)
and [Dev Interrupted podcast](https://linearb.io/dev-interrupted/podcast/dex-horthy-humanlayer-rpi-methodology-ralph-loop).
Related coinage from the same "12-Factor Agents" manifesto: **Factor 3, "Own your context
window"** — build your own dense token format instead of accepting the default message-array
shape, because "optimizing your own format allows you to get the most out of today's LLMs." —
[12-factor-agents, Factor 3](https://github.com/humanlayer/12-factor-agents/blob/main/content/factor-03-own-your-context-window.md)

**Context creep** — informal HN-commenter coinage for the specific failure mode of iterative
agent pipelines where "output becomes input becomes output again" and each run's prompt silently
grows ("it's easy to keep adding 'just one more thing' to a prompt without realizing each run is
getting heavier") — the fix offered was a hard pre-declared token budget. — Ask HN thread, ["Burned
$250 in tokens on Day 1 with OpenClaw"](http://hn.algolia.com/api/v1/items/47162495), comment by
user akssassin907. [unverified beyond this single thread]

**Doom loop** — "The agent researches again → fills context → compacts again → forgets again," or
more generally a `Compact → Research → Compact → Research` cycle that signals the agent has lost
the thread; community advice is "if you see a 'Compact → Research' cycle happen twice, hit Ctrl+C
immediately." Also used generically for repeated-mistake loops in AI coding tools. — synthesis via
[getunblocked.com](https://getunblocked.com/blog/ai-agent-doom-loop/) and
[amazingcto.com](https://www.amazingcto.com/where-ai-struggle-doom-loops/). [likely genuine
community usage, exact coiner unclear]

**"Lost the plot"** — informal shorthand for what context rot/distraction feels like from the
user's chair: "the model loses the detail about why something failed or what it found, and the
agent has lost the thread." Used loosely across blogs; not attributable to one coiner but
functions as the layperson's synonym for context rot/distraction. [unverified provenance, common
usage]

**Ralph Wiggum (the "Ralph" technique / Ralph loop)** — Coined and popularized by **Geoffrey
Huntley**, mid-2025. A deterministic bash loop (`while :; do cat PROMPT.md | claude ...; done`
in spirit) that "feeds an AI's output — errors and all — back into itself until it dreams up the
correct answer." Directly a *context-management* technique, not just an automation trick: "a
context window is effectively just `malloc`ing an array... every interaction... allocates data to
the array," so the loop deliberately **kills and restarts the agent process every iteration**,
giving it a clean context window each time, with state persisted only via the filesystem (specs,
progress files) rather than conversation history. Huntley's design mantra: pick "the best, most
important item and only do one" per iteration, to minimize allocation per pass. Practitioners
describe the empirical trigger for needing this discipline as the same "dumb zone": "LLM quality
degrades non-linearly past roughly 60–70% context fill." — synthesis of
[LinearB/Dev Interrupted podcast](https://linearb.io/dev-interrupted/podcast/inventing-the-ralph-wiggum-loop-creator),
[codecentric.de deep dive](https://www.codecentric.de/en/knowledge-hub/blog/the-ralph-wiggum-loop-autonomous-code-generation-with-a-fresh-context),
[ralph-wiggum.ai](https://ralph-wiggum.ai/).

**Kitchen sink session** — Anthropic's own named anti-pattern (see Rituals section above) for
mixing unrelated tasks in one context. — [best-practices doc](https://code.claude.com/docs/en/best-practices)

**Dead context** — Ryan Spletzer's term: "instructions, tool definitions, and metadata sitting in
the context window that aren't contributing to the task at hand," with the memorable framing "a
bigger context window is analogous to more RAM on a machine with a memory leak." —
[Shedding Dead Context](https://www.spletzer.com/2026/03/shedding-dead-context/)

**Context tax** (MCP-specific) — Peter Steinberger's framing for MCP server overhead permanently
consuming context budget regardless of whether it's used that turn; paired with his empirical
claim that "most models kinda give up if you have > 40" MCP servers/tools active at once. —
[@steipete on X](https://x.com/steipete/status/1921838640855994616)

**Structured note-taking** — Anthropic's term (context-engineering post) for the Pokémon-agent /
`NOTES.md` pattern: the agent writes durable notes outside the window and re-reads them after a
reset, functioning as agentic long-term memory without fine-tuning. Closely related to /
overlapping with **recitation** (see Rituals section) and to Claude Code's built-in to-do list
tool, which the same post cites as a shipped example of the pattern.

**Just-in-time context / progressive disclosure** — Anthropic's terms for lazy-loading context
(glob/grep-driven file retrieval, CLAUDE.md-as-index) instead of front-loading everything, mirrored
by Thoughtworks' Birgitta Böckeler as "lazy loading" and "context interfaces" (tools/MCP/Skills the
agent can reach for on demand rather than being handed up front). —
[Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents),
[Martin Fowler / Böckeler](https://martinfowler.com/articles/exploring-gen-ai/context-engineering-coding-agents.html)

**Compound(ing) engineering** — Dan Shipper and Kieran Klaassen (Every), coined publicly in an
Aug 2026-referenced X thread: "Each feature should make subsequent features easier to build, not
harder." Concretely operationalized as the **Plan → Work → Assess → Compound** loop, where the
"Compound" step turns each session's lessons into permanent CLAUDE.md/doc updates — makes context
curation a *pipeline stage*, not an afterthought. —
[@danshipper on X](https://x.com/danshipper/status/1957469842178441523),
[Every: Compound Engineering](https://every.to/chain-of-thought/compound-engineering-how-every-codes-with-agents)

**Goal engineering** — Greg Ceccarelli's proposed sibling term to prompt/context engineering: the
practice of authoring a checked-in, git-diffable **goal+rider document pair** per unit of agent
work (see Rituals section) — positioned explicitly as "one round" granularity, between
prompt-engineering (one turn) and context-engineering (one inference call). —
[gregceccarelli.com/goal-engineering](https://www.gregceccarelli.com/goal-engineering) [single-source coinage, not yet widely adopted — flag as niche/unverified-adoption]

**"Index sickness"** — a term from a single arXiv preprint ("Written by AI, Managed by AI: Semantic
Space Control and Index Sickness Elimination Across 391 Consecutive Sessions," arXiv:2606.19121)
describing degradation of an AI-managed knowledge/session index over many consecutive sessions.
Included for completeness since it surfaced in search, but **[unverified / not seen used by
practitioners outside this paper]** — do not treat as established community vocabulary.

---

## Practitioner notes

**Simon Willison** (independent, simonwillison.net)
- Amplified and named Drew Breunig's failure taxonomy the same week it was published, adding the
  fix-side vocabulary (tool loadout, context quarantine, pruning, offloading) —
  [How to Fix Your Context](https://simonwillison.net/2025/Jun/29/how-to-fix-your-context/).
- Practices high session parallelism with long resume gaps rather than single marathon sessions —
  reports "11 concurrent inactive sessions," resumed "after hours or days" —
  [Recent Claude Code Quality Reports](https://simonwillison.net/2026/Apr/24/recent-claude-code-quality-reports/).
- General posture: mechanical/hook-driven consistency over relying on the model to remember
  things turn to turn (cited approvingly by other practitioners, e.g.
  [merlinmann's gist](https://gist.github.com/merlinmann/34708a72be7ed2e3aad013a2bbeb7f83)).

**Boris Cherny** (creator of Claude Code, Anthropic)
- Context minimalism as an explicit design philosophy for both the product and his own usage:
  "give Claude the goal, constraints, and retrieval path, then let it work" rather than
  front-loading context — [howborisusesclaudecode.com](https://howborisusesclaudecode.com/).
- CLAUDE.md as a **team-level living postmortem**: every observed mistake becomes a permanent
  instruction, PR comments tagged `@.claude` feed the file — same source.
- Runs ~10–15 concurrent sessions (terminal + browser + mobile) rather than one long-lived one,
  treating parallel narrow sessions as the default unit of work, not an optimization.
- Prefers Opus-with-thinking over Sonnet for coding despite latency, valuing reliability/tool-use
  quality enough to eat the speed cost — [via search synthesis, The Neuron / InfoQ coverage].

**Anthropic (engineering blog / product docs, institutional voice)**
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
  (Sep 2025): compaction, structured note-taking, sub-agent architectures as the three main levers;
  "find the smallest possible set of high-signal tokens."
- [Claude Code best practices / code.claude.com/docs/en/best-practices](https://code.claude.com/docs/en/best-practices):
  the most complete single distillation of community folklore into policy — kitchen-sink-session,
  two-strikes-then-clear, CLAUDE.md pruning test, subagents-for-investigation, adversarial review
  subagents, Writer/Reviewer pattern.
- [Maximizing the value of your Claude Code sessions](https://claude.com/blog/maximizing-the-value-of-your-claude-code-sessions):
  tactical token-hygiene tips — `/clear` between tasks, `/compact` before stepping away (cache
  expires in ~1hr), `@`-mention files instead of describing them, quiet flags / subagents for noisy
  commands, `/rewind` instead of `/compact` for "that went wrong" moments (rewind is cache-free).

**Geoffrey Huntley** (independent, AU)
- Ralph Wiggum loop: fresh-context-every-iteration as *the* solution to context rot for
  long-horizon autonomous work, not a hack — "one task per iteration, then die and restart" is the
  whole technique. Positions context-window fill as literally a memory-allocation problem.

**swyx / Shawn Wang (Latent Space)**
- Frames context engineering as infrastructure-level, on par with data engineering for training:
  "Context engineering is as important to inference as data engineering is to training." Latent
  Space's ongoing coverage (AI Engineer Summit, "Agent Engineering" pieces) treats context/harness
  engineering as a first-class discipline alongside evals and observability, but Latent Space
  functions more as an aggregator/amplifier of others' concrete techniques (Horthy, Breunig, etc.)
  than a source of swyx's own novel context rituals. —
  [latent.space](https://www.latent.space/)

**Dex Horthy** (HumanLayer, "12-Factor Agents")
- The "dumb zone" finding (see Glossary) and the blunt corollary "you cannot prompt your way out of
  it" is arguably the single most quotable, most falsifiable piece of folk wisdom in this whole
  survey — it converts "keep context small" from vague advice into an actual claimed threshold.
- RPI methodology (Research → Plan → Implement) as the structural fix: force intermediate design
  artifacts before code, explicitly to avoid ever filling the window with speculative
  back-and-forth. — [12-factor-agents](https://github.com/humanlayer/12-factor-agents)

**Thorsten Ball** (Sourcegraph Amp, "Register Spill" newsletter)
- ["How to Build an Agent"](https://ampcode.com/notes/how-to-build-an-agent) defines an agent
  minimally as "an LLM with access to tools, giving it the ability to modify something outside the
  context window" — useful framing for a plugin: the context window is inherently the *volatile*
  half of agent state, tools/filesystem are the durable half.
- Sourcegraph/Amp's "80% problem": agents nail the visible 80% of a task but silently miss the 20%
  that's outside their context (cross-repo/cross-cutting effects); the fix framed as "a
  deterministic, repository-wide view," not a bigger window.

**Every / Dan Shipper & Kieran Klaassen**
- Compound engineering (see Glossary) — the Plan→Work→Assess→Compound loop is essentially a
  formalized, tooled version of "leave a note for the next Claude," productized as a Claude Code
  plugin with slash commands per phase (`/ce:plan`, etc.).
- Boris Cherny has publicly cross-endorsed it, which is a signal it's converged with, not diverged
  from, Anthropic's own internal practice —
  [@danshipper](https://x.com/danshipper/status/2007192398368248084).

**Armin Ronacher** (independent, ex-Sentry, Flask creator)
- Delegates side-work to a *different* CLI agent (Gemini CLI) specifically to avoid spending
  Claude Code's own context budget on it — a cross-tool version of the subagent-firewall pattern.
- Codebase-shape advice as a context-management technique in itself: prefer simple, boring,
  low-churn languages/patterns (Go, PHP, "basic Python," plain SQL over ORMs) *because* they need
  less contextual explanation for an agent to work in correctly. —
  [lucumr.pocoo.org/2025/6/12/agentic-coding](https://lucumr.pocoo.org/2025/6/12/agentic-coding/),
  [lucumr.pocoo.org/2025/7/30/things-that-didnt-work](https://lucumr.pocoo.org/2025/7/30/things-that-didnt-work/)

**Peter Steinberger** (ex-PSPDFKit, OpenClaw)
- Loudest voice against MCP-as-context-cost: "MCP is a context tax"; advocates CLI-first tool
  design (`bash` + `jq`) so the *agent* filters output before it hits context, instead of the
  server dumping a fixed schema regardless of relevance. Runs a monthly "Essential Reading for
  Agentic Engineers" roundup that functions as a de facto community reading list —
  [steipete.me/posts](https://steipete.me/posts).

**Birgitta Böckeler** (Thoughtworks, via Martin Fowler's site) — bonus practitioner, not in the
original ask list but directly on-topic and worth citing:
- "An agent's effectiveness goes down when it gets too much context" — argues for incremental
  CLAUDE.md/config growth rather than front-loading, and draws the instructions-vs-guidance
  distinction (specific directives vs. general conventions) as a way to decide what's worth the
  permanent token cost. — [martinfowler.com](https://martinfowler.com/articles/exploring-gen-ai/context-engineering-coding-agents.html)

---

## Shared hacks/snippets

**Context-% statuslines** (the most common hack by far — dozens of gists/repos exist):
- [captivus's statusline gist](https://gist.github.com/captivus/9ccd08b2760215e91b9c820c6d7bcde3) —
  parses `context_window.used_percentage` from the statusline JSON, color-codes green/yellow/red
  at 65%/85%, and — notably — **also writes the percentage to `/tmp/claude-context-percentage.txt`
  specifically so the agent itself can read its own context state and act on it** (e.g. adjust
  verbosity, suggest a reset). This is the single most directly relevant hack for a
  self-management plugin: it closes the loop from "display to human" to "expose to agent."
- [plribeiro3000's statusline](https://gist.github.com/plribeiro3000/17354a5214a97f59c8fef9e37b30c87e) —
  color-coded bar showing tokens remaining *before autocompact specifically* (not just window
  size), model, and branch.
- [sirmalloc/ccstatusline](https://github.com/sirmalloc/ccstatusline) — full customizable
  statusline framework: context %, context bar, session/weekly usage, block timer widgets.
- [ohugonnot/claude-code-statusline](https://github.com/ohugonnot/claude-code-statusline) — adds
  rate-limit/quota tracking alongside context window and git branch.
- Official support for this exists natively: Anthropic's docs point to `used_percentage` as "the
  simplest accurate context state" and ship an [interactive context-window visualizer](https://code.claude.com/docs/en/context-window)
  showing exactly what consumes tokens at startup.

**PreCompact hooks (save state before lossy summarization):**
- [affaan-m/everything-claude-code `pre-compact.js`](https://github.com/affaan-m/everything-claude-code/blob/main/scripts/hooks/pre-compact.js) —
  generates an LLM summary of the session and writes it into the session file wrapped in HTML
  comment markers, immediately before Claude Code's own (lossier) compaction runs.
- [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem) — a full plugin built around
  this idea: hooks capture everything the agent does during a session, compress it with an LLM,
  and inject the relevant compressed memory back into *future* sessions automatically (works
  across Claude Code, Codex, Gemini, Copilot, etc., not just Claude Code) — installed via
  `/plugin marketplace add thedotmack/claude-mem`.
- Community-reported GitHub feature requests asking Anthropic to ship this natively:
  [PreCompact hook: allow Claude to take actions before compaction, #43733](https://github.com/anthropics/claude-code/issues/43733) and an older
  [#15923](https://github.com/anthropics/claude-code/issues/15923) — i.e. the hook exists now, but
  users have wanted stronger/more official guarantees around it than they feel they have.
- Common pattern across several independent implementations: dump structured state to
  `SESSION_STATE.md` at PreCompact time — current task/progress, key decisions + rationale, files
  modified + why, errors encountered + fixes, next steps. (Multiple near-identical variants found;
  treat the specific field list as folklore-standard rather than any one canonical source.)

**UserPromptSubmit reminder injectors:**
- Documented native mechanism: whatever a `UserPromptSubmit` hook prints to stdout is injected as
  extra context ahead of the user's actual prompt, before Claude processes anything —
  [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks). Common uses found: inject
  current git branch/PR number per turn, log every prompt with a timestamp for later audit, block
  prompts matching policy/secret patterns. Explicit community caution: firing this on *every*
  prompt wastes tokens — use sparingly / conditionally, not as a permanent tax on every turn.
- [disler/claude-code-hooks-mastery](https://github.com/disler/claude-code-hooks-mastery) is the
  most complete public reference repo for the full hook lifecycle (30 events), useful as an
  implementation reference for whatever hooks this plugin ends up needing.

**Env vars as blunt context-management instruments** (all community-discovered, not prominently
documented at the time of the tweets):
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000` — lower the effective window at which auto-compact
  fires, e.g. to 400k instead of using the full 1M — [@trq212 (Anthropic)](https://x.com/trq212/status/2044653085415604473)
- `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` + `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` — Sikkema's combo to
  force compaction at ~140k tokens.
- `CLAUDE_CODE_TMPDIR` — redirect the scratchpad off ephemeral `/tmp` for containerized/remote dev
  environments.

---

## Top 10 load-bearing practices (ranked)

1. **Proactive compaction at ~50–60% of the window, never waiting for auto-compact.**
   *Why it works*: auto-compact fires only once the model is already in (or past) the "dumb zone,"
   so its own summary is generated by a degraded model reasoning over degraded context — a
   compounding failure. Compacting early means a *sharp* model writes the summary. Multiple
   independent sources converge on "60% not 95%" almost verbatim.
   *Plugin automation*: track `context_window.used_percentage` continuously (already exposed to
   hooks/statusline); at a configurable soft threshold, proactively suggest or auto-trigger
   `/compact` with a task-aware custom instruction — but only at a natural task boundary (see #2),
   never mid-tool-call.

2. **One task, one session — `/clear` between unrelated work, not mid-task.**
   *Why it works*: prevents "kitchen sink sessions" and stops corrections from turn N polluting the
   prompt for turn N+40; empirically, chained unrelated tasks in one session correlate with
   context-drift regressions.
   *Plugin automation*: detect task-boundary signals (todo-list fully checked off, PR opened,
   explicit "done" acknowledgment) and prompt/offer a clean `/clear` + fresh-session handoff rather
   than silently letting the user keep typing into the same thread.

3. **Externalize state to a plan/progress file before it's needed, not as disaster recovery.**
   *Why it works*: this is what makes `/clear` and `/compact` *lossless in effect* — the transcript
   can die because the durable facts (decisions, file list, next step) live on disk, git-diffable
   and re-readable by a cold-started agent. This is the mechanism behind Ralph Wiggum, Compound
   Engineering's `plan.md`, and Anthropic's own SPEC.md workflow simultaneously.
   *Plugin automation*: maintain an auto-updated `PROGRESS.md`/`PLAN.md` as a first-class
   artifact the plugin writes to continuously (not just at compact-time), and treat "does this
   file accurately reflect reality" as the actual completion signal for compaction/clearing safety.

4. **Subagents as context firewalls for anything read-heavy or noisy.**
   *Why it works*: the byproduct (30 file reads, full test output, doc crawl) is large; the useful
   conclusion is small. Isolating the noisy part in its own context window means it never dilutes
   the main thread's attention budget, and never has a chance to trigger distraction/confusion in
   the primary agent.
   *Plugin automation*: heuristically detect exploration/investigation-shaped requests
   ("investigate X," "find where Y is used," "run the tests and tell me what failed") and default
   to dispatching them as subagents automatically, returning only a distilled summary to the main
   thread — Anthropic's docs already gesture at this ("use subagents to investigate X") but leave
   the *decision* of when to do so to the user; a plugin could make that judgment call itself.

5. **CLAUDE.md as a pruned, git-checked-in living document — curation, not accumulation.**
   *Why it works*: CLAUDE.md is loaded on *every* turn, so it's the one piece of context paying
   rent for the entire session; bloat here recreates context-rot's core mechanism (important
   signal diluted by low-value tokens) on every single turn, compounding across the whole session.
   *Plugin automation*: periodically run the "would removing this line cause a mistake?" audit
   automatically (Spletzer's ritual, done by hand today) and propose a diff instead of waiting for
   a human to notice bloat; flag lines that duplicate what a linter/hook already enforces.

6. **Redirect noisy tool output to files/summaries instead of raw dumps into context.**
   *Why it works*: command output "stays there for the rest of the session" once it's in the
   transcript — it isn't just a one-time cost, it's rent paid on every subsequent turn until a
   compact/clear. Quiet flags, `jq`-filtering, and 30k-char auto-file-writes all attack this at
   the source instead of cleaning it up later.
   *Plugin automation*: wrap high-output-risk tool calls (test runners, build logs, broad greps)
   with automatic truncation-to-file-plus-preview by default, not opt-in.

7. **Git commit as the real checkpoint; `/rewind` for cheap in-session undo.**
   *Why it works*: commits are permanent, tool-agnostic, and outlive the session; `/rewind` is
   free (no cache invalidation) but session-scoped and blind to Bash-driven changes — the two are
   complementary, not substitutes, and conflating them is a common failure mode.
   *Plugin automation*: nudge/auto-commit at detected milestones (tests passing, plan-step
   complete) specifically so that `/clear` or a crash never loses code state, independent of
   whatever the conversation-level checkpoint system captures.

8. **A pre-compaction/pre-clear "handoff note" written automatically, not just on request.**
   *Why it works*: the manual version of this ("write me a handoff prompt") only helps if the user
   remembers to ask before running out of context — exactly the moment they're least likely to
   have the presence of mind to do it. The `pre-compact.js` / claude-mem pattern of doing this via
   a hook, unconditionally, removes the failure mode of forgetting to leave a note.
   *Plugin automation*: this is close to a direct spec — a `PreCompact`/pre-`/clear` hook that
   always generates and persists a structured summary (goal, decisions, files touched, next step),
   independent of and in addition to whatever Claude Code's own compaction summary produces.

9. **Context-% visibility surfaced continuously, including back to the agent itself, not just the human.**
   *Why it works*: users consistently report not noticing degradation until it's already caused a
   mistake ("watched two of the five bars vanish instantly"). The captivus gist's trick of writing
   the percentage to a file the *agent* can read is the key insight: the model can adapt its own
   behavior (verbosity, when to suggest wrapping up) if it has this signal, not just the human.
   *Plugin automation*: expose current usage/threshold-proximity to the agent's own reasoning
   (e.g., injected via `UserPromptSubmit` or a tool the agent can call), so self-management
   decisions ("I should compact now," "this investigation should be a subagent") can be made by
   the agent in-context rather than requiring the human to watch a statusline.

10. **Compact-with-custom-instructions, tuned to what this specific task needs preserved.**
    *Why it works*: a generic compaction summary optimizes for recall of *everything*, which
    under Anthropic's own compaction-prompt guidance means starting from maximum recall and only
    then trimming for precision — but a task-aware instruction ("preserve the file list and test
    commands," "summarize the debugging dead-ends, keep the schema") gets a much higher-precision
    summary for free, because the task itself defines what's high-signal.
    *Plugin automation*: instead of one generic compaction instruction, derive a compact-time
    instruction from the live plan/progress file's "in progress" section — i.e. let the plugin's
    own state-tracking feed back into how Claude Code compacts, closing the loop between practice
    #3 and native compaction.

---

## Sources

- [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Anthropic: Claude Code best practices (code.claude.com/docs/en/best-practices)](https://code.claude.com/docs/en/best-practices)
- [Anthropic/Claude blog: Maximizing the value of your Claude Code sessions](https://claude.com/blog/maximizing-the-value-of-your-claude-code-sessions)
- [Anthropic: Explore the context window (interactive visualizer)](https://code.claude.com/docs/en/context-window)
- [Anthropic: Hooks reference](https://code.claude.com/docs/en/hooks)
- [Chroma Research: Context Rot](https://www.trychroma.com/research/context-rot)
- [Drew Breunig: How Long Contexts Fail](https://www.dbreunig.com/2025/06/22/how-contexts-fail-and-how-to-fix-them.html)
- [Drew Breunig: How to Fix Your Context](https://www.dbreunig.com/2025/06/26/how-to-fix-your-context.html)
- [Simon Willison: How to Fix Your Context](https://simonwillison.net/2025/Jun/29/how-to-fix-your-context/)
- [Simon Willison: Recent Claude Code Quality Reports](https://simonwillison.net/2026/Apr/24/recent-claude-code-quality-reports/)
- [Simon Willison: tag/claude-code index](https://simonwillison.net/tags/claude-code/)
- [Geoffrey Huntley / LinearB: Inventing the Ralph Wiggum Loop](https://linearb.io/dev-interrupted/podcast/inventing-the-ralph-wiggum-loop-creator)
- [codecentric: The Ralph Wiggum Loop deep dive](https://www.codecentric.de/en/knowledge-hub/blog/the-ralph-wiggum-loop-autonomous-code-generation-with-a-fresh-context)
- [ralph-wiggum.ai](https://ralph-wiggum.ai/)
- [Boris Cherny tips (howborisusesclaudecode.com)](https://howborisusesclaudecode.com/)
- [Boris Cherny 13 tips (GitHub mirror)](https://github.com/shanraisshan/claude-code-best-practice/blob/main/tips/claude-boris-13-tips-03-jan-26.md)
- [Boris Cherny on X](https://x.com/bcherny/status/2007179832300581177)
- [Dex Horthy / HumanLayer: 12-Factor Agents](https://github.com/humanlayer/12-factor-agents)
- [12-Factor Agents, Factor 3: Own your context window](https://github.com/humanlayer/12-factor-agents/blob/main/content/factor-03-own-your-context-window.md)
- [Pragmatic Engineer: Context engineering with Dex Horthy](https://newsletter.pragmaticengineer.com/p/context-engineering-with-dex-horthy)
- [Dev Interrupted: Dex Horthy on Ralph, RPI, and escaping the Dumb Zone](https://linearb.io/dev-interrupted/podcast/dex-horthy-humanlayer-rpi-methodology-ralph-loop)
- [Thorsten Ball: How to Build an Agent](https://ampcode.com/notes/how-to-build-an-agent)
- [Sourcegraph: Agentic Coding in 2026 / the 80% problem](https://sourcegraph.com/blog/agentic-coding)
- [Every: Compound Engineering — How Every Codes With Agents](https://every.to/chain-of-thought/compound-engineering-how-every-codes-with-agents)
- [Every: How I Use Claude Code to Ship Like a Team of Five](https://every.to/source-code/how-i-use-claude-code-to-ship-like-a-team-of-five-6f23f136-52ab-455f-a997-101c071613aa)
- [creatoreconomy.so: How to Make Claude Code Better Every Time (Kieran Klaassen)](https://creatoreconomy.so/p/how-to-make-claude-code-better-every-time-kieran-klaassen)
- [Dan Shipper on X (compound engineering coinage)](https://x.com/danshipper/status/1957469842178441523)
- [Armin Ronacher: Agentic Coding Recommendations](https://lucumr.pocoo.org/2025/6/12/agentic-coding/)
- [Armin Ronacher: Agentic Coding Things That Didn't Work](https://lucumr.pocoo.org/2025/7/30/things-that-didnt-work/)
- [Peter Steinberger: Essential Reading for Agentic Engineers](https://steipete.me/posts/2025/essential-reading)
- [Peter Steinberger on X: MCP context clutter](https://x.com/steipete/status/1921838640855994616)
- [Peter Steinberger on X: mcporter/jq filtering](https://x.com/steipete/status/1989853716921434227)
- [Martin Fowler / Birgitta Böckeler: Context Engineering for Coding Agents](https://martinfowler.com/articles/exploring-gen-ai/context-engineering-coding-agents.html)
- [LangChain: Context Engineering for Agents (write/select/compress/isolate)](https://www.langchain.com/blog/context-engineering-for-agents)
- [Manus: Context Engineering for AI Agents — Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [Ryan Spletzer: Shedding Dead Context](https://www.spletzer.com/2026/03/shedding-dead-context/)
- [Greg Ceccarelli: Goal Engineering](https://www.gregceccarelli.com/goal-engineering)
- [Albert Sikkema: Why I Shrunk Claude Code's Context Window Back to 200k](https://www.albertsikkema.com/ai/development/tools/2026/04/23/smaller-context-window-better-claude-code.html)
- [Thariq Shihipar (Anthropic) on X: CLAUDE_CODE_AUTO_COMPACT_WINDOW](https://x.com/trq212/status/2044653085415604473)
- [HN/Algolia: Ask HN — Burned $250 in tokens on Day 1 with OpenClaw](http://hn.algolia.com/api/v1/items/47162495)
- [HN/Algolia search: "context rot"](http://hn.algolia.com/api/v1/search?query=context%20rot&tags=story)
- [affaan-m/everything-claude-code: pre-compact.js hook](https://github.com/affaan-m/everything-claude-code/blob/main/scripts/hooks/pre-compact.js)
- [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)
- [disler/claude-code-hooks-mastery](https://github.com/disler/claude-code-hooks-mastery)
- [captivus: statusline context-% gist](https://gist.github.com/captivus/9ccd08b2760215e91b9c820c6d7bcde3)
- [plribeiro3000: statusline gist](https://gist.github.com/plribeiro3000/17354a5214a97f59c8fef9e37b30c87e)
- [sirmalloc/ccstatusline](https://github.com/sirmalloc/ccstatusline)
- [ohugonnot/claude-code-statusline](https://github.com/ohugonnot/claude-code-statusline)
- [Piebald-AI/claude-code-system-prompts: scratchpad directory](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/system-prompt-scratchpad-directory.md)
- [qubytes.substack.com: scratchpad.md in Claude Code](https://qubytes.substack.com/p/scratchpad-claude-code-context-window-task-state)
- [GitHub Issue #43733: PreCompact hook feature request](https://github.com/anthropics/claude-code/issues/43733)
- [GitHub Issue #15923: pre-compaction hook feature request](https://github.com/anthropics/claude-code/issues/15923)

**Gaps / things not found despite searching**: no `/catchup` command exists (checked directly);
direct Reddit thread text was not fetchable (site blocked for WebFetch, no working JSON-API
fallback found in the time available) so r/ClaudeAI and r/ClaudeCode material here is
second-hand via search-engine summarization, not primary-sourced — treat individual "community"
quotes attributed only to "r/ClaudeAI" as [likely paraphrase, not verbatim]. X/Twitter access was
also indirect (via WebSearch, not direct API), so tweet quotes are as rendered by search snippets
and could contain minor transcription drift.
