---
name: testing
description: >-
  Load BEFORE writing, changing or reviewing tests or a red run: a feature or bugfix test, a test
  for a guard or migration, choosing unit vs integration vs e2e, mocks and fakes, fixtures, a flaky
  or slow test, a mutation check, a spike, port or rehearsal, or following superpowers'
  test-driven-development, which these rules override.
---

# Testing

The Tests rule holds what must hold; this skill is how to meet it with fewer, stronger tests.

## Overrides of superpowers' test-driven-development

- **Which suite:** while working, run the tests the brief or the Tests rule names (what you
  touched and its direct dependents). The full suite runs at the rule's checkpoints, and in a
  subagent-driven plan the coordinator runs it; ignore TDD's "run the whole project suite".
- **How many:** one test per behaviour a caller or user would notice, not one per function or
  method. A helper reached only through a public API gets its own test only when it is complex
  (a parser, a matcher) or hard to reach.
- **Red first or after:** writing the test first is preferred and its red run on unchanged code
  counts. A red run by mutating the finished code is valid evidence too; code written before its
  test is not deleted.

## Choose the level and the boundary

- Test at the lowest level that still proves the behaviour, through the public API the way callers
  use it. Never repeat the same check at a second layer.
- e2e only for what smaller tests cannot see: wiring across processes, the real browser, one test
  per important user journey. A unit test runs in milliseconds; a slow one is mislabelled.
- Testing private methods or internal call order gives false failures on refactors and false
  passes when the app is broken.

## Fewer tests, more cases

- A new case for an existing behaviour is a new row in its table or parameterised test, not a new
  test function. A new behaviour gets its own test instead of growing an old one.
- Write no test to raise coverage, for logic-free code (getters, constants, framework calls) or
  that restates the implementation (a change detector). Delete or rewrite one you find.
- Assert only what the behaviour needs; a test that prints values instead of asserting them is
  not a test. Assert a parsed value, never a substring of its serialised form (`'page=2'` matches
  `per_page=250`).
- A test per event order (click twice, response before render) only for an order that already
  failed.

## Expected values come from the requirement

- Derive each expected value from the requirement or a worked example, never by running the code
  and copying its output: such a test encodes the current bug.
- A bug fix's test is red on the base commit (run it in a worktree at base): no hand mutation
  needed.

## Doubles

- Prefer the real implementation when it is fast and deterministic, then a fake, then a stub at
  the network edge (HTTP, mail, a third-party API). Mock an interaction only when the interaction
  is the contract (a sent mail, a charged card).
- Prefer asserting state (the row, the file, the response) over interactions.
- A double stays faithful: pair it with one check against the real thing, give it the real
  artefact's type (a real `.exe`, not a `.cmd`), recognise a call by its content, not its argv
  position, and check the stub's own exit code.
- Restore every patch, env change and global in teardown or `finally`.

## Red runs

- Break the covered code once per new test and remove each new runtime check once; it must go red
  on its own assertion. Name what you mutated. A mutation that resolves to the same value, or a run
  failing on an unrelated check, proves nothing.
- **Security, money and data-integrity checks** (a capability or ownership test, input validation
  that guards storage, a visibility filter, a sanitiser, a transaction or lock, an amount): mutate
  each match branch, each row of a pattern or name table, each stage (prefilter, chunk edge, page,
  batch) and the wiring (unhook the callback). A check whose removal stays green is untested
  (write the test) or redundant (delete it, naming the layer that guarantees it, once an independent implementation of that layer, such as a second parser for markup, also fails to break it); a comment states only guarantees a test proves.
- **Other full-risk work** (shared infrastructure, migrations): one mutation per branch, up to 5
  per check, chosen by what callers rely on, the branches the brief lists under Risk first. Here
  only, a surviving mutant
  that changes nothing a caller or user relies on (a log line, an internal capacity, an equivalent
  form) gets no test; list it in the report with why nothing relies on it, so a reviewer
  can challenge it. Branches left unmutated by the cap are listed too.
- A generated or table-driven matrix (routes × roles, a fixture table) is broken once per rule it
  encodes (each capability, each ownership kind, the wiring that feeds the table), not per cell.
  A table that overrides a default rule (holidays, closures) is tested only with inputs whose
  result differs from the default (a holiday on a weekday), so deleting the table turns it red.
- Where the stack has a mutation tool, run it on the changed files (see `rigger-core:stack-tooling`)
  for branch mutations; table rows, stages and wiring stay hand mutations.
- `python "<rigger-testing plugin folder>/tools/safe-edit/safe_edit.py"` with `--path`, `--find`,
  `--replace`, `--count`, `--run` and `--expect-ran` mutates in place, runs and restores (usage in its
  header); never restore a mutation with `git checkout`.
- A scratch copy of a worktree deletes its `.git` first: the copied pointer makes git act on the
  real worktree.
- Python: a mutant that keeps the file's size and mtime second can load a stale `.pyc`; run each
  mutant with `-X pycache_prefix=<dir per mutation>` (`-I` ignores `PYTHONPYCACHEPREFIX`) and read
  the failing case.
- A test that claims a runtime or shell (Windows PowerShell 5.1, a Python version) spawns it, and
  the invocation that runs the code under test prints its version; a separate probe call stays
  green when the code hard-codes another one. Red-run it by hard-coding the other one there.
- Report per low-risk test one line: test id → mutation → failing assertion line.

## Checks that can report nothing

- A leak check, sweep, lint, audit, aggregate, eval score or exclusion pattern fails on 0 files read
  or a missing input and gets a positive control shaped like the claim (a paraphrase for "same
  intent"); a tool that misses it is unfit, not tuned. One known-answer control per input class.
- A diagnostic or spike gets an input whose answer you know, read back before the run; an
  aggregate's top bucket is checked for a catch-all; a before/after measurement finds its run by a
  unique marker in the probe's prompt, never as the newest file. "Already fixed" needs the reported symptom reproduced in the environment the report names (a nearby fix in history proves nothing), or says it was not reproduced.
- A refusal, control or rehearsal is judged by its printed reason line (and no traceback), never by
  a non-zero exit alone; a script's mismatch counter sets its exit code.
- A denial test (401/403, a redirect to login) sends a request that succeeds without the gate (valid
  fields, token, CSRF) and asserts the exact error code. An auth test, or a spike that decides
  whether to stop, runs each side it claims (each guard, each token type) with a real credential
  through the real guard, never an `actingAs`-style shortcut.
- A port or rewrite is proved by running original and copy on the same input and listing every
  difference, the guards its callers supplied included.
- Input read from outside gets one test per wrong shape (a list where a dict is expected, a number
  where a list is expected; a string iterates harmlessly and kills no `isinstance(…, list)` mutant), each proved by its mutant.
- An exclusion glob prints the count of what it excluded as its control: `rg -g '!<glob>'` with an
  absolute search path excludes nothing (globs match the path as given), so run it from the root
  with `.` as the path.
- A regex run on user- or model-supplied text gets a backtracking probe: 40 repeats of each token of
  every repeated group, under a time bound. Where alternatives can match the same text two ways,
  split into words and walk them in code; rerun the probe after each fix.
- A read-only or permission check on POSIX reads the mode bits (`st_mode & 0o222`), not only
  `os.access(W_OK)`, which root always passes; its test also runs as root.

## Fixtures and real output

- Fixtures as hostile as production: the app's own wrappers and providers; ids, slugs and
  timestamps derived by the framework, never hand-set; the worst-case row (build it when the data
  has none), including one that breaks the contract a guard relies on (a record marked deleted that still has
  a public URL); a case no layer between the test and the code can fake. A parser's fixtures are lines
  copied from real files, one per line shape.
- State only the values that matter to the test; a builder or factory fills the rest.
- A fake secret in a fixture is assembled at runtime (`'sk_' + 'live_…'`), never one literal in a
  provider's shape (Stripe, GitHub, AWS, Slack): the host's push protection rejects the push
  (GitHub GH013) after every local gate passed.
- A loop that deletes rows or moves them out of its own filter gets a test that seeds more than two
  chunks: paging by offset skips every second chunk; iterate by key (`chunkById()`, `lazyById()`)
  or use one query.
- Asserting the options passed to a library, or finding a CSS rule's text, is not testing the
  result: for visual, network or storage behaviour assert on the real output (pixels, HTTP
  response, DB row, file).
- Code that attaches to routes or config at boot gets one test that boots as production does
  (Laravel: `route:cache`, `config:cache`) and then requests the route.

## Flakiness and speed

- Wait for the condition (auto-waiting assertion, poll the state), never a sleep.
- Each test owns its data and assumes nothing about order; run with shuffled order and a printed
  seed (commands per stack in `rigger-core:stack-tooling`).
- Repeat new and changed tests several times before merge (`--repeat-each` for Playwright,
  `rigger-core:e2e-playwright-speed`); most flaky tests are flaky from the day they are written.
- Time is pinned: a frozen clock ("today" the real clock cannot reach, an exact budget value),
  timing-sorted output compared as a set, a spawn budget with room for an interpreter stall, and a
  timing counts as "alone" only while nothing else runs.
- A test that passes only on retry is labelled flaky and gets a task, never counted as a plain pass.

## Agent pitfalls to check in your own tests

- Hard-coded expected values or special cases that make one test pass.
- Deleted or loosened assertions, `skip`/`only`/`xfail`, regenerated snapshots without a cause found.
- "All tests pass" without the command and its output.
