# Eval case inventory

One row per case of a plugin's `evals/` suite, run with `claude plugin eval`. Each case comes from a
failure seen in real sessions. A row exists only when the rule or skill is in the plugin today, that
kind of content reaches an eval session, and a grader of the eval tool can read the outcome (these
cases use `regex`, `llm` and `tool_used`; `claude plugin eval --help` lists the others). Cases ask
in prose and read the final message: no case grants a shell (native Windows refuses a granted shell,
the WSL run is not proven).
`python -I scripts/eval_suite_check.py --cases evals/CASES.md plugins/<plugin>` checks that the
case folders and this table match.

## rigger-rules

| case | failure it came from | rule meant to prevent it | what a correct run does | grader type | tag |
|---|---|---|---|---|---|
| zero-files-check | a lint reported clean after reading zero files | `tests.md`: a check that can report nothing fails on 0 files read | says a check that exits 0 on a missing folder or 0 files scanned is not ready and asks for a positive control | `regex` | native |
| vacuous-first-pass | new tests passed at once and were accepted in self-review | `tests.md`: a test is evidence only if it can fail | does not accept a test that passed at once; asks for a red run or a mutation | `regex` | native |
| fail-closed-bypasses | a guard was called fail-closed while paths around it existed | `defects.md`: list the paths around a guard before calling it fail-closed | lists paths that skip the filter instead of confirming the claim | `regex` | native |
| removal-takes-neighbours | removing a page section also removed an unnamed tile inside it | `scope.md`: removing a container keeps what else renders inside it | lists what else renders in the container and keeps it | `llm` | native |
| full-e2e-per-task | the full e2e suite ran inside every task of a work loop | `tests.md`: full e2e only at merges, otherwise the changed specs | runs the changed specs per task and the full suite at the merge | `regex` | native |

## rigger-core

Each case also has a `tool_used` grader on `Skill` (`arm: with-only`) next to the graders below.

| case | failure it came from | skill meant to prevent it | what a correct run does | grader type | tag |
|---|---|---|---|---|---|
| release-stops-on-gh-failure | a release script carried on after a failed `gh` call | `git-workflow` (Release) | stops on a failed `gh` call and takes the PR number only from a `/pull/<n>` URL | `regex` | native |
| add-commit-push-chain | `git add` aborted on a stale pathspec and the push went out anyway | `git-workflow` (Commits) | chains add, commit and push with `&&` and reads `git status --short` first | `regex` | native |
| clean-merge-claim | a "merges cleanly" claim hid a test the merge broke | `git-workflow` (Branches) | backs a "merges cleanly" claim with `git merge-tree` output | `regex` | native |
| separate-agent-subagent | "a separate agent" was read as a new session | `orchestrating-subagents` | reads "a separate agent" as a subagent, not a new session | `regex` | native |

## rigger-windows

| case | failure it came from | skill meant to prevent it | what a correct run does | grader type | tag |
|---|---|---|---|---|---|
| bash-in-powershell | bare `bash` typed in PowerShell started WSL | `windows-dev-env` (Shells) | says bare `bash` in PowerShell is WSL and points at the Bash tool or Git Bash | `regex` | native |
| worktree-with-junctions | `git worktree remove --force` emptied a junction's target | `windows-dev-env` (junctions) | removes the junctions first, then the folder | `regex` | native |
| rmtree-readonly-objects | `shutil.rmtree` failed on read-only git objects | `windows-dev-env` (PowerShell traps) | passes an error handler that makes the path writable and retries | `regex` | native |
| utf8-env-hides-bug | `PYTHONUTF8=1` hid a launcher encoding bug | `windows-dev-env` (Text encoding) | tests with `PYTHONUTF8` cleared and passes `-X utf8` | `regex` | native |
| commit-message-file-path | a commit message file under a deep path failed as too long | `windows-dev-env` (Long paths) | puts the message file in a short scratch folder | `regex` | native |

## Not measurable

| case | reason |
|---|---|
| windows: a shell guard fires on `bash <script>`, `git commit -F -`, `git checkout -- <path>` | needs the PreToolUse shell guard and a granted shell; a granted shell is refused natively, the WSL run is not proven |
| windows: a Python heredoc turns `\\b` into a backspace | needs a granted shell and a written file |
| windows: `Stop-Process` on a package-manager wrapper leaves the child running | needs live processes |
| core and rules: rules injected only for a tool-using session (state file written under the config dir) | needs a granted shell or Write and a session id; the file outcome cannot be read without them |
| core: `web-ui-traps`, `e2e-playwright-speed`, `stack-tooling` | outcome is a browser or a tool run, not a final message |
