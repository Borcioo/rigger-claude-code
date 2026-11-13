# Implementer brief and report template

For any implementation agent (`rigger-core:worker`, `rigger-core:implementer`, `general-purpose`). Copy per dispatch
(or per project as `implementer-common.md`) and fill every `<…>`. A field that does not apply
says "n/a" with a reason; never delete it silently.

---

## Brief

**Task:** <one sentence> · **Done when:** <observable acceptance criteria> · **Round:** <0 = first
build, or fix round n and the finding ids it takes; from n = 2 the re-plan's path>

**Waits for:** `<exact input>` released by `<event>`; until then: `<read-only prep>`. Questions go to the parent session, never to the user; end no turn with an ask, and none while your own background run is going: wait inside the turn and end it with a result or a question.

**Map**
- EDIT: `<path:lines - what is there>`, one per line; the only files the agent changes.
- FIX ZONE: `<paths>` or "none"; where it may fix the root cause of the task (at most 5 changed
  lines per file, only when the gate covers it) and must list the fix under ROOT CAUSE.
- GATE: the first command under "Gate commands" below.
- PINNED BY: every test that asserts the exact text in EDIT (`rg -l` a distinctive phrase under the test folders), or "none".
- Anything else the change needs (code, accounts, settings, data on any site): the agent stops and
  asks.

**Workspace (yours alone)**
- Worktree: `<absolute path>` on branch `<branch>` from `<base commit>`.
- Dev-server / e2e port: `<port>` (env var `<NAME>`); never reuse a server you did not start.
- Scratch subfolder: `<scratch folder>/<task-id>/`, where `<scratch folder>` is a temp folder
  outside every repo that the coordinator names (the session's scratchpad, or one under the OS temp
  folder) — every temp file goes here.
- The site the user clicks through runs a pinned checkout; do not touch it.

**Gate commands (run exactly these; nothing broader)**
- Run by the coordinator before dispatch, each on a known-good and a known-bad input: `<command>`
  -> `<last output line>`, exit `<n>`.
- While working: `<focused unit tests>`; run each new spec as it is written, not all at the end.
- Before reporting: `<lint on the files the commit holds>` · `<focused e2e, one engine>`
- A fix round that changes a shared helper (identity, sanitizer, permission): `<full integration suite>`.
- A gate still running after 15 minutes: stop it and report. The coordinator runs the full suite.
- A time bound changed in a test: report file:line, old → new, the reason, and one mutation that
  still fails the test. A second widening: stop and report instead.
- A new e2e spec is added to the spec→source map in the same commit: `<map test command>`, run
  before you commit, not after.

**Machine load:** at most `<N>` building/testing agents at once; e2e `workers=<n>`; timing suites
`<names or none>`: final tally alone, or pass at `<N>` of 10. Heavy runs take
the machine lock where the project has one (e.g. a project's `tools/e2e-lock.mjs`); without one, run them
only within the limit above and report a run you had to wait for.

**Skills:** `<skill name>` per skill the task needs, with the step it covers; `rigger-core:worker`
and `rigger-core:reviewer` load them themselves, `rigger-core:implementer` gets none.
**Browser:** which one, or none. **Risk:** low / full, as the tests rule defines it (rigor follows risk, check by
check). With full, list the
checks that get mutations (`<capability and ownership rules, validation, visibility, ...>`);
the rest of the change is low.

**Pitfalls**
- Project: `<project CLAUDE.md, Pitfalls section>` — read it first.
- This session: <paste every lesson captured so far>.
- If rigger-windows is installed (Windows): absolute paths for `[IO.File]`/`[IO.Directory]`; commit with
  `git commit -m @'…'@` or `-F <file>`, never `-F -`; no `node_modules`/`vendor` junctions in
  worktrees (removing the worktree empties the target; a `vendor` junction makes Composer load the
  main checkout's code); source edits only with the Edit tool or `safe-edit.ps1 -Find -Replace
  -Count`, never a heredoc or regex script; superpowers helper scripts (`sdd-workspace`, `task-brief`,
  `review-package`) only through the Bash tool (Git Bash) by absolute path — `bash` typed in
  PowerShell starts WSL.
- Red runs: in a worktree, or in place — with rigger-windows installed — through
  `<rigger-windows root>/tools/safe-edit/safe-edit.ps1 -Run`; never restored with `git checkout`.
- Escapes are built at runtime (`"\\" + "u0141"`), never typed through Write/Edit expecting a literal character.

**Design:** `<board path>` the task reproduces, or n/a; the review compares the built screen with
it (layout, alignment, order).

**Plan context:** <paste the plan's Global Constraints and Review Focus — superpowers
`task-brief` drops them, so they reach the agent only through this field>.

**Deliverable:** `<path>`, never a report file — write a skeleton after the first batch of work and
update it in place. Return the report below in your final reply; the coordinator saves it.

---

## Report

The report's first line is `STATUS: DONE`, `STATUS: PARTIAL`, `STATUS: BLOCKED` or `STATUS: NEEDS_CONTEXT`.
An approval you need from the user goes in the Result line, as the question to ask them.

1. **Result:** one line why that status. Blocked is a valid result. Partial and blocked
   name the unmet Done-when item and carry the exact call (or the stopped gate command), its raw
   output, and one alternative route tried with its raw output.
2. **Commits:** `<hash> <subject>` per commit.
3. **Gates run:** per gate, pasted verbatim: the command, the last lines of its output (counts) and
   the exit code; plus the duration.
4. **Red runs:** for every new guard or test the brief lists as full — the command, the guarded
   code removed (on a copy or worktree), the assertion that failed, and proof the mutation
   executed (a log or counter); for the rest, the red run of the test as written.
5. **ROOT CAUSE:** `file:line - what changed` for each FIX ZONE edit, or "none".
6. **Raw evidence** for every bug or cause claimed: the console line, raw response, log entry or
   measured value, read where it happened — not a paraphrase.
7. **`git diff --stat`:** paste it, plus `git diff --stat <base> -- <test dirs>`; a diff far larger
   than the change means line-ending churn. Any existing test or assertion you removed, loosened or
   skipped is listed with the reason.
8. **Read these:** `path:line` of every hunk that touches the database schema or migrations,
   settings, permissions or auth; new dependencies; files outside the plan — or "none".
9. **Left behind:** worktrees, junctions, servers, ports, temp files — and what you cleaned up.
10. **For the user to test / open questions.**
11. **Lessons:** anything that would recur in another task.

---

## Reviewer checklist

- [ ] Each **Done when** item is listed with pass or fail and the evidence that shows it (command
      output, screenshot, `file:line`); an item without evidence fails.
- [ ] Each new guard has a red run that failed on its assertion, with the mutation proven executed.
- [ ] Each new guard lists the paths around it (another client, a followed redirect, an
      overridable function, the browser), each with a test.
- [ ] Each check, eval or aggregate that can report nothing shows its positive control caught
      (output pasted) and fails on 0 files or a missing input.
- [ ] Every tool that reads, writes or deletes paths was tested with a symlink or junction (inside
      the root pointing out, as a target, in a source), a trailing-dot or trailing-space name and a
      directory where a file is expected; guards resolve the real path before acting.
- [ ] Fixtures use the app's own wrappers/providers, framework-derived values and worst-case data;
      a parser's fixtures are lines copied from real files, one per line shape.
- [ ] Every claim about a third-party plugin, setting or embed cites what was observed (network
      list, DOM snapshot, source line) or says unverified.
- [ ] Every claimed cause cites raw evidence.
- [ ] Gate output is pasted as report item 3 asks, not described.
- [ ] No workaround of an environment, tool or config defect: a source inside EDIT/FIX ZONE is
      fixed there within the FIX ZONE limits; otherwise it is reported as blocked, with the
      follow-up under item 10.
- [ ] `git diff --stat` matches the size of the change.
- [ ] Every file in `git status --porcelain` is named in the report's FILES; every changed file
      outside EDIT is a FIX ZONE file listed under ROOT CAUSE.
- [ ] No existing test or assertion was removed, loosened or skipped (`skip`, `only`,
      `markTestSkipped`, `@group` exclusions, rewritten snapshots) without a stated reason; the
      test-dir diff confirms it.
- [ ] "Read these" lists every risky hunk (database schema or migrations, settings,
      permissions or auth, dependencies, files outside the plan).
- [ ] Comments, docs and skills hold only what changes what the reader does: no sources, history,
      model names or "we decided" (those belong in the PR or commit body).

## Merge checklist (coordinator)

1. `git diff --name-only <base>..<other>` — list the shared paths it changed (test helpers,
   `_shared`, fixtures, config).
2. For each changed shared helper, grep this side's call sites; each must rely only on the
   helper's contract, not on a side effect the other side removed.
3. After each reviewed task on the main line, merge it into side branches that touch the same area
   (or branch side tasks from the latest reviewed head).
4. Run the full suite after the merge, not only the files that conflicted.
