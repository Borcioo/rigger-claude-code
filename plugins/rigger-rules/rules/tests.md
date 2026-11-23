## Tests

- While changing code, run the tests covering what you touched and its direct dependents. Full
  suite after a group of related tasks, before a commit others build on, before and after every
  merge, and for shared infrastructure (auth, routing, build, schema, a helper with several
  callers; fix rounds too); full e2e only at merges and shared infrastructure, else the changed
  specs. Say which tests ran and why. Lint every file the commit holds (or everything), never a
  hand-picked list.
- A test is evidence only if it can fail: show it red on its own assertion, not on an import or
  syntax error, with output proving it ran (`1 passed`); a name filter that matches nothing can
  exit 0. No conditional assertions. A TDD red run on unchanged code counts; otherwise break the
  covered code and name the mutation. Red runs: in a worktree, or in place only through
  `safe-edit.ps1 -Run` (needs rigger-windows).
- Rigor follows risk, check by check. A check that enforces security, money or data integrity
  (capability, ownership, validation guarding storage, visibility, sanitiser, lock, amount) is
  broken once per branch, table row, stage and wiring; other shared infrastructure and migrations
  once per branch up to 5 per check, the rest listed in the report; there only, a surviving mutant
  nothing relies on gets no test but is listed with why. Everything else gets one test per
  behaviour a user would notice and one red run per test. A brief or report names which checks it
  treated as full.
- Tests check the requirement; they do not define it. Never special-case or hard-code to pass one;
  a test you believe is wrong is reported, not worked around. Never get to green by weakening a
  test: removing, loosening or skipping one needs a reason in the report, with the test-dir diff.
- Anything that can report "nothing" (a leak check, a sweep, a lint, an audit, an aggregate, an
  eval score, an exclusion pattern) fails on 0 files read or a missing input and gets a positive
  control it must catch; a diagnostic or a spike gets an input whose answer you already know.
- A "test" on the real code path writes real data: before running one against a shared database,
  site or device, check what it writes and force its dry-run flag.
