## Tests

- While changing code, run the tests that cover what you touched and its direct dependents, not
  the whole suite or e2e after every edit.
- Full suite at checkpoints: after a group of related tasks, before a commit others build on,
  before and after every merge, and when the change touches shared infrastructure (auth, routing,
  build, schema, a helper with several callers — in fix rounds too); full e2e only at merges and
  shared infrastructure, otherwise the changed specs. Say which tests you ran and why. Lint every
  file the commit holds (or everything), never a hand-picked list.
- A test is evidence only if it can fail: show the red run failing on the assertion, not on an
  import or syntax error, with output proving the test ran (`# tests 1`, `1 passed`); a name filter
  is a regex; one that matches nothing can exit 0. No conditional assertions (`if (x) assert …`).
- Anything that can report "nothing" (a leak check, a sweep, a lint, an audit, an aggregate, an
  eval score, an exclusion pattern) fails on 0 files read or a missing input and gets a positive
  control it must catch, shaped like the claim (a paraphrase for "same intent"); a tool that misses
  it is unfit, not tuned. A diagnostic or a spike gets an input whose answer you already know, read
  back before the run; an aggregate's top bucket is checked for a catch-all; a before/after
  measurement finds its run by a unique marker in the probe's prompt, never as the newest file.
- Rigor follows risk, check by check, not task by task. The next bullet applies in full to a
  check that enforces security, money, data integrity or a migration (a capability or ownership
  test, input validation that guards storage, a visibility filter, a sanitiser, a transaction
  or lock, an amount) and to shared infrastructure. Everything else in the same change (UI
  components, DTO and locale plumbing, list and form screens) gets one test per behaviour a
  user would notice, one red run per test, and a test per event order only for an order that
  already failed. A brief or report names which checks it treated as full. These counts
  replace the checklist of a TDD skill (such as `superpowers:test-driven-development`) where they
  differ.
- Break every new test once (mutate the code it covers: each match branch, each row of a pattern or name table, each stage such as a
  prefilter or chunk edge, and the wiring, e.g. unhook the callback) and every new runtime check
  once (remove it): a test must go red on its own assertion (a TDD red run counts on unchanged code); a red-run
  claim names what it mutated. A mutation that resolves to
  the same value, or a run failing on an unrelated check, proves nothing; a check whose removal
  stays green is untested (write the test) or redundant (delete it, naming the layer that
  guarantees it). Red runs: in a worktree, or in place only through
  `<rigger-windows root>/tools/safe-edit/safe-edit.ps1 -Run` with rigger-windows installed (usage in its header).
  A generated or table-driven matrix (routes × roles, a fixture table) is broken once per rule
  it encodes (each capability, each ownership kind, the wiring that feeds the table), not once
  per cell.
- A port or rewrite is proved by running original and copy on the same input and listing every
  difference, the guards its callers supplied included; reading it check by check misses whole checks.
- A refusal, a control or a rehearsal is judged by its printed reason line (and no traceback), never
  by a non-zero exit alone; a script's mismatch counter sets its exit code.
- Time in a test is pinned: a frozen clock ("today" the real clock cannot reach, an exact budget
  value), timing-sorted output compared as a set, a spawn budget with room for an interpreter stall,
  and a timing counts as "alone" only while nothing else runs.
- Never get to green by weakening a test: removing, loosening or skipping an existing test or
  assertion needs a reason stated in the report or reply, with the test-directory diff.
- Fixtures as hostile as production: the app's own wrappers/providers; ids, slugs and timestamps
  derived by the framework, never hand-set; the worst-case row (build it when the data has none);
  a case no layer between the test and the code can fake.
- A "test" that drives the real code path writes real data: before running one against a shared
  database, site or device, check what it writes and force its dry-run flag explicitly.
- Asserting the options passed to a library, or finding a CSS rule's text, is not testing the
  result: for visual, network or storage behaviour, assert on the real output (pixels, HTTP
  response, DB row, file).
