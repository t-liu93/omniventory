# AGENTS.md · Omniventory

> For every agent working in this repo (Claude Code, Codex, any model). This file holds only the **stable rules + signposts**; concrete, milestone-evolving content lives in `docs/plan/`, so **don't duplicate it here — read the source**.
>
> (`CLAUDE.md` is a symlink to this file; the two are always the same file. Edit either name and you edit both.)

## Working language (non-negotiable — read this first)

**Reply in whatever language the user writes to you in.** User writes Chinese → answer in Chinese; user writes English → answer in English. Re-evaluate every turn and default to the user's language; switch only when the user **explicitly** asks for another language. This file and the other repo docs are written in English for future open-sourcing — that is a documentation choice and **does not** set the conversation language.

## What this project is

**Omniventory** is a self-hosted **"three-in-one" inventory system**: it unifies three normally-separate needs into **one data model** —

1. **Best-before / expiry + advance reminders** — food / medicine / consumables, with proactive "N days ahead" alerts.
2. **Durable-goods ledger** — serial number, warranty, value, multi-level location hierarchy, photos; full lifecycle tracking.
3. **Consumable stock** — in/out movement ledger, minimum-stock thresholds, low-stock alerts.

Personal use first, intended to be **open-sourced** later. **Status: pre-implementation** — the project has been scoped (see the investigation below), but the **tech stack and architecture are not chosen yet**.

## Where to read first (signposts — don't duplicate their content here)

- **Origin / inspiration / cross-project insight** (Homebox vs InvenTree vs Grocy data-model comparison, and why we're building our own): `docs/inspiration/investigation.md`. This is the authoritative "why we exist" doc; it is self-contained.
- **Roadmap + global constraints + milestone map**: `docs/plan/roadmap.md` *(to be created)*.
- **Before implementing anything**: read the current milestone's `docs/plan/milestones/M<x>.md`. The **active milestone = the one marked 🟡 in the roadmap progress table** ("how far we've got" is tracked only there, never in this file).
- **Design docs are self-contained**: every design / implementation-reference doc under `docs/plan/` stands on its own. Don't bake design decisions into this file, and don't treat `investigation.md` as a design spec — it's inspiration; the real design lands in the design docs.

## Tech stack & commands — TBD

The stack is **deliberately undecided** (the investigation defers it on purpose). Until **M0 scaffolding** lands and picks one:

- Don't assume a stack, framework, package manager, or directory layout.
- This section and a concrete command list get filled in **once M0 is chosen**, and only then.

## Workflow & quality gates

- **Atomic changes**: one independently-deployable, test-backed small thing at a time.
- **Single developer, no forced PR**: self-test + CI green ⇒ merge straight to `main` (open a branch/PR only when you want a human review).
- **Definition of Done** (every step passes): lint + type-check + tests green; build passes; **logic that's easy to get wrong must have unit tests** — quantity math, expiry / lead-time date computation, stock in/out and consumption order (e.g. FIFO), threshold / low-stock triggers; no convention violations. *(Exact commands and any contract/codegen gates arrive with the stack at M0.)*

## Implementation / Review briefs

- **Per-step implementation brief**: after each implementation round (planning doesn't count), write a brief under `review-notes/` **in the author's working language**, covering at least: (a) what this round implemented; (b) automated-test results; (c) manual walkthrough steps. In orchestrator mode, name it `review-notes/M<x>-step<n>-impl.md`.
- **Milestone-level report** (end of milestone): once all steps of a milestone are done, also produce `review-notes/M<x>-report.md` — ① thorough; ② readable by the author; ③ containing the **complete manual walkthrough for this milestone** (stitching together each `M<x>.md`'s "🟢 deploy self-test points"). This is the input for the author's manual walkthrough.
- **Review input**: when the author asks for a review, read the brief the author points to; if unspecified, read the latest brief under `review-notes/`, then review against the incremental diff and the relevant design docs.
- **Review output**: only when there are findings / change requests, write a review report under `review-notes/` (in the author's working language); if there are none, just say so in chat — don't create a file.

## Commit conventions (hard rules)

- Commit messages use **English Conventional Commits**: `feat:` / `fix:` / `docs:` / `docs(plan):` / `refactor:` / `chore:` …
- **No AI / Claude attribution of any kind**: no `Co-Authored-By`, no "authored by Claude" or similar.
- **Commit / push only when the author explicitly asks.**

## Commit rhythm (implement / rework / wrap-up)

> The author drives a feature's commit rhythm with three keywords; **the keyword itself is the explicit authorization to commit** (this refines the blanket "commit only when asked" above — no conflict). All three follow the commit conventions (English Conventional Commits, no AI attribution).
> **In orchestrator mode these happen automatically per step**, and autosquash is **per-step** (each atomic step squashed into one commit), not per-feature — see "Agent orchestration".

1. **Implement** ("实现" / "implement"): when done, set the Conventional Commits message and `git commit` one round for the feature.
2. **Rework** ("返工" / "rework"): **don't open a new standalone commit** — fixup the implementation commit being reworked: `git commit --fixup=<target impl commit sha>`.
3. **Wrap up** ("收尾" / "done"): when the author calls the feature finished, auto-squash its implementation commit(s) + all fixups into **one** commit.
   - Command: `GIT_SEQUENCE_EDITOR=: git rebase --autosquash <commit before the feature's first commit>` (interactive `-i` isn't available in this environment; run non-interactive autosquash via `GIT_SEQUENCE_EDITOR=:`).
   - Autosquash folds each fixup back into its target impl commit; if the feature produced **multiple** impl commits, squash those together in the same rebase too, so the feature leaves exactly one commit.

## Agent orchestration (implementation execution model)

> Implementation supports two execution modes. **Manual is the default**; only when the author **explicitly names orchestrator mode / "just generate it"** do you run the full auto loop below. Design docs (`docs/plan/milestones/M<x>.md`) should be written **self-contained + with blind-review points**, so both modes can hang off them.

### Two modes

- **Manual (default)**: the author asks you to implement a step and produce a brief = manual mode. **Don't auto-spawn sub-agents, don't auto-run the review/fix loop, don't auto-commit** (commits still follow the "commit rhythm" keyword authorization). **Absent an explicit orchestrator-mode call, always this.**
- **Orchestrator (full-auto)**: the author opens a fresh Opus (extra-high reasoning) conversation and **you are the orchestrator**, driving sub-agents through the specified step(s) / milestone via the loop below. **Naming orchestrator mode is itself explicit authorization for this round's commits (impl / fixup / per-step autosquash).**

### Three sub-agent types (model defaults; the prompt can override)

- **implementer / fixer**: same class, consistent logic; default **Sonnet + high reasoning**.
- **reviewer**: default **Opus + extra-high reasoning**.
- When the author names a different model / reasoning level in the prompt, **the prompt wins**.

### Per-step loop (orchestrator mode)

The orchestrator decides and advances step by step (1 → 2 → …, one step per iteration). Run a full round per atomic step before moving on:

1. **Implement (implementer)**: spawn a clean implementer; instructions must include:
   - Implement **only the current step** — no freelancing (don't do other steps, don't sneak in refactors).
   - **Test-complete**: cover happy flow + corner cases.
   - **Don't pollute the host**: clean up any temp verification artifacts; **never touch the host's real environment** (DB / containers / files).
   - Write the step's implementation brief (see "Implementation / Review briefs").
   - Land one **implementation commit** (= this step's feature commit).
2. **Blind review (reviewer)**: spawn a **fresh** reviewer; give it **only**: (a) the milestone design doc (`M<x>.md` + roadmap); (b) the just-written brief; (c) the step's diff. **No access to the implementer's conversation / reasoning** (black-box blind review). Focus: ① does it follow the design doc exactly; ② any drift from the design doc; ③ code bugs + latent risks.
   - Findings → write a review report under `review-notes/` (the author may read it).
   - No findings → step done.
3. **Rework (fixer)**: on findings → spawn a fixer; input = **design doc + that review report**; land a **`--fixup` commit** (pointing at this step's impl commit, see "commit rhythm").
4. **Re-review**: after rework, **spawn a reviewer again**; keep rework → re-review while **new findings** remain, until **none**. **Rework cap = 5 rounds**; if findings remain after 5, **stop and escalate to the author**.
5. **Close the step**: once the step's impl + all fixups are settled, do **one per-step autosquash** (command as in "commit rhythm", base = the commit before this step's impl commit). ⇒ at milestone completion, **one commit per step**.
6. **Next step**: repeat 1–5 until all steps of the milestone are done.

### Milestone wrap-up

- All steps done → produce the **milestone-level report** (`review-notes/M<x>-report.md`, see "Implementation / Review briefs").
- The author does a **manual walkthrough** against it; walkthrough change requests are handled in **manual conversation** (no auto loop). As the project matures, the manual walkthrough converges to **once per milestone** (per-step gate = automated tests green + blind review with no findings).

## Maintaining this file

Only edit this file when **foundations** change: the tech stack gets chosen, the rules / conventions / orchestration above change, or a new agent tool is added. **Milestone progress does not touch this file** — that only updates `docs/plan/`.
