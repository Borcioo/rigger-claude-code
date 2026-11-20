# Port from the private harness to Rigger

Source: private repo, branch `develop`, squash commits after Rigger 0.3.0 (2026-11-17). Naming:
`foreto-X` becomes `rigger-X`; no mydevil, intake or wcag plugin; everything English; no company,
client or person names, hosts or paths. Sizes: S = at most ~30 changed lines and no new hook or
tool, M = a skill, rule or script change with tests, L = a new hook or tool or more than ~300
lines. Hours are agent working hours (code and tests together).

## Items

| # | Source | What it changes | Applies to Rigger | Target files | Size | Needs | Est. h |
|---|---|---|---|---|---|---|---|
| 1 | #41 | PreToolUse hook timeouts 10 s to 20 s; a repo test pins them | Yes. Rigger has 2 PreToolUse handlers (wrap_up, AskUserQuestion), not 4; the weak-test-guard pair arrives with #30 | `rigger-core/hooks/hooks.json`, `tests/test_repo.py`, side-session-guard test | S | none | 0.3 |
| 2 | #42 | coordinate: heavy parts at most 2 at once, light parts grouped; an over-cap start becomes the user's card | Yes; "owner" becomes "user" as Rigger's coordinate.md already says | `rigger-core/commands/coordinate.md`, `coord-state/tests/test_coordinate_wiring.py` | S | none | 0.3 |
| 3 | #35 | shell-guard junction fix line says "in a call of its own"; windows-dev-env tool-guard bullet corrected | Yes (net change only: the loop-value work inside the PR was reverted in it) | `rigger-windows/tools/shell-guard/shell_guard.py`, its `tests/test_rules.py`, `skills/windows-dev-env/SKILL.md` | S | none | 0.4 |
| 4 | #31 | secret-mask fixtures use provider-neutral tokens | Yes; Rigger splits the literal (`"sk_" "live_..."`), the neutral form drops the provider shape altogether | `rigger-core/tools/token-audit/tests/test_archive.py`, `rigger-lessons/tests/test_lessons_lint.py` | S | none | 0.2 |
| 5 | #24 | rules size ceiling test | Already in Rigger 0.3.0 (`RulesSizeTests`, ceiling 30293 B); nothing to port | none | n/a | none | 0 |
| 6 | #25 | plan autonomy | Already in Rigger 0.3.0 (changelog 0.3.0) | none | n/a | none | 0 |
| 7 | #23 | owner CLAUDE.md rule | Skip (the owner's own file) | none | n/a | none | 0 |
| 8 | #43 | presentation | Skip | none | n/a | none | 0 |
| 9 | #27 | `testing` skill (137 lines); Tests rule cut to must-holds; agents, brief template and stack-tooling point at the skill | Yes. The rules text shrinks, so lower `RULES_CEILING_BYTES` | `rigger-core/skills/testing/SKILL.md`, `agents/*.md`, `templates/implementer-brief.md`, skills `e2e-playwright-speed`, `stack-tooling`; `rigger-rules/rules/{tests,plans-main,skills}.md`, `test_plugin_rules.py`, README | M | none | 1.5 |
| 10 | #40 | coordinate briefs load `rigger-core:testing`; the full suite and gate stay with the coordinator | Yes, but the brief names the testing skill, so it waits for #27 (a dangling skill name otherwise) | `coordinate.md`, `test_coordinate_wiring.py` | S | #9 | 0.2 |
| 11 | #32 | general test pitfalls move from CLAUDE.md into the testing skill; weak-test-guard and INSTALL.md line tweaks | Yes; the weak-test-guard hunk needs #13 | Rigger `CLAUDE.md`, `testing/SKILL.md`, `shell-guard/INSTALL.md`, later `weak_test_guard.py` + test | M | #9, #13 | 0.8 |
| 11a | #33 | eval `testing-skill-loads` with a negative control | Yes, after #9; plus `evals/CASES.md` rows | `rigger-core/evals/testing-skill-loads/*`, `evals/CASES.md` | S | #9 | 0.4 |
| 12 | #44 + #45 | curated skill lessons (#44) and their post-review tightening (#45) | Apply the end state of both in one commit per skill: `e2e-playwright-speed`, `git-workflow`, `orchestrating-subagents`, `testing`, `web-ui-traps` (core), `rigger-wp-traps`. Drop all mydevil hunks and the `deploy-patterns.md` line. #45's drift-lint test hunk applies to Rigger's `test_drift_lint.py` | the skills above, `rigger-windows/tools/drift-lint/tests/test_drift_lint.py` | M | #9 for the testing-skill hunks | 1.5 |
| 13 | #30 | weak-test-guard: a PreToolUse hook that denies a commit weakening tests without a stated reason | Yes (owner priority: tests). New hook, 726 lines of code, 814 of tests; the `Test-change:` trailer wording needs a check for harness-specific text; changes the handler counts pinned in `DuplicateHookTests` and the PreToolUse count from #1 (2 to 4) | `rigger-core/tools/weak-test-guard/*`, `hooks/hooks.json`, `tests/test_repo.py` | L | #1 | 3 |
| 14 | #29 | curated lessons (2026-11-17): git-workflow, orchestrating, stack-tooling, testing, web-ui-traps, windows-dev-env, wp-traps, rules `defects.md` and `verify.md` | Yes, minus the owner CLAUDE.md hunk (project pitfalls go into Rigger's `CLAUDE.md` only where Rigger-relevant). Rules shrink: lower the ceiling again | skills as listed, `rigger-rules/rules/{defects,verify}.md`, ceiling | M | #9, #12 (same skills, order matters) | 1.2 |
| 15 | #28 | maintainer rules split from what every colleague loads: `lessons-maintainer.md`, `proposal_check.py`, `rules_start.sh` per plugin, curate-lessons skill | Partly. Rigger has no `HARNESS_MAINTAINER` mode. Port only if the user wants a maintainer mode in the public edition; otherwise take the `rules_start.sh` change and drop the rest. Decision needed | `rigger-lessons/*`, `rigger-rules/rules/*`, every plugin's `rules_start.sh` | L | #14 | 3 |
| 16 | #26 | verified reference on testing practices (1009 lines) + sweep.py allowed hosts | Yes as `docs/research/testing-practices.md`; dry-run the sweep on it first (documentation hosts need `ALLOWED_DOMAINS` entries); rewrite any company or person mention | `docs/research/testing-practices.md`, `scripts/sweep.py`, `tests/test_sweep.py` | M | none (cited by #9) | 1.0 |
| 17 | #34 | an eval suite loads its dependency: `eval_stage.py`, `eval_suite_check.py` and `eval_summary.py` checks, CASES.md | Yes; the rigger-rules evals gain the `rigger-core` dependency; one new script and 3 fixtures | `scripts/eval_stage.py`, `scripts/eval_suite_check.py`, `scripts/eval_summary.py`, `plugins/rigger-rules/evals/*/prompt.md`, `tests/*`, fixtures, CASES.md | L | none | 2 |
| 18 | #36 | grade the right answers of the first real-model run; `eval_summary.py` only-real handling | Yes after #17 (same files); testing-skill graders after #11a | `scripts/eval_summary.py`, graders, fixture, CASES.md | M | #17, #11a | 1.2 |
| 19 | #38 | shell-guard reads variables, words and locations only in certain shapes (+646 lines) | Yes (windows safety); Rigger's `shell_guard.py` may have diverged, so check the base before porting; INSTALL.md rewrite | `rigger-windows/tools/shell-guard/*` | L | #3 | 3.5 |
| 20 | #37 | coordinate side sessions in the terminal CLI: `cli-host.md`, `coord_state.py host`, stub tests | Yes; large new tests, a stub-API host test | `rigger-core/tools/coord-state/*`, `coordinate.md`, side-session-guard test | L | #2, #10 | 3 |
| 21 | #39 | the stub's host call is found by plugin path, not a word | Only after #20 (Rigger has no `test_coordinate_host_stub.py`); fold into #20 or follow it. The CLAUDE.md pitfall lines go to Rigger's `CLAUDE.md` | `test_coordinate_host_stub.py`, `CLAUDE.md` | S | #20 | 0.3 |

## Batches

| Batch | Items | Est. h | Status |
|---|---|---|---|
| 1 (now) | 1 (#41), 2 (#42), 3 (#35), 4 (#31); #24 and #25 confirmed already present | 1.2 | PR 12 (open) |
| 2 (testing core) | 16 (#26), 9 (#27), 10 (#40), 11a (#33) | 3.1 | PR on branch `feature/port-batch-2-testing`, based on batch 1 |
| 3 (lessons into skills) | 12 (#44+#45), 14 (#29) | 2.7 | PR on branch `feature/port-batch-3-lessons`, based on batch 2 |
| 4 (weak-test-guard) | 13 (#30), 11 (#32) | 3.8 | waits |
| 5 (evals) | 17 (#34), 18 (#36) | 3.2 | waits |
| 6 (coordinate CLI) | 20 (#37), 21 (#39) | 3.3 | waits |
| 7 (shell guard, maintainer mode) | 19 (#38), 15 (#28) | 6.5 | waits; #28 needs a user decision |

Batches 2 to 7 total about 22.6 agent hours. The order follows the dependencies and the priority
(core, lessons, tests): batch 2 unlocks 3, 4 and 5; batch 6 needs only batch 1.

## Defaults taken in Batch 1

- #40 is left out of Batch 1: its brief text and test name `rigger-core:testing`, a skill Rigger has
  not got until #27. It moves to batch 2.
- #24 and #25 need no change. #31 is ported although Rigger already splits the literal, because the
  neutral token also removes the provider-shaped sample from the repo.
- No version bump or tag in the PR: CONTRIBUTING says a user-visible change adds a line under
  `## [Unreleased]` in `CHANGELOG.md`, and a release is tagged from `main` with the user's OK. The
  title says `0.3.1` as the intended next release only.

## Owner decisions and process

- The owner decided to port everything that applies, the testing skill (#27) and its dependents
  included, and to continue past Batch 1 with one PR per batch, each with its own full gate and
  `--verify`. Foreto, MyDevil, Nexus and WCAG items, #23 and #43 stay skipped. Nothing is merged,
  tagged or released without the owner's OK.
- Batches are stacked: each batch branch starts from the previous one and its PR targets the
  previous branch, so each diff shows only its own commits (the batches touch the same files, for
  example `coordinate.md`). Merge them in order.
- Source patches are applied with the paths and names renamed (`foreto` to `rigger`); hunks that do
  not fit Rigger's text are redone by hand. Batch 2: `tests.md` is replaced by the source's cut text
  with the safe-edit wording adapted, the rules ceiling is lowered to 28059 bytes, and
  `testing-skill-loads` is added to Rigger's `evals/CASES.md` without a `source_ref` column.
- Batch 3: Rigger's skills are leaner than the harness's, so a curated hunk whose anchoring text is
  absent was left out (git-workflow's site-release comparison, the recount of agent totals in
  orchestrating-subagents, every MyDevil hunk, the drift-lint MyDevil fixture, the owner CLAUDE.md
  hunk of #29). #44 and #45 were applied as one end state; rejects were redone by hand in their
  final wording. `rigger-wp-traps` gained a Page caching and an Environment guards section.
