---
name: review
description: >-
  Load BEFORE reviewing a change, a branch or a pull request, or verifying an agent's claim about
  code: which files are in scope, which per-path rules and language checklists apply, and how the
  review closes with the rigger.reviewer/1 JSON.
---

# Review

The procedure of a review. The report format, the severities and the closing JSON are the
reviewer agent's; this skill says how to get there. `<base>` and `<head>` are the commits the brief
names.

## 1. Scope

- Run rigger-core's `review_scope.py` from the reviewed repository, with `<root>` the root of
  the rigger-core plugin, whose root the rules' `Plugin root:` line gives:
  `sh '<root>/bin/py.sh' '<root>/tools/review-scope/review_scope.py' --base <base> --head <head> --rules-out <scratch folder>`. The scratch folder is the one the brief gives; with none given,
  leave out `--rules-out` and say in the report that project rules files were not read.
- Each line is `<verdict><TAB><path>`. Review every path whose verdict starts with `review`
  (`review: binary added` included; `review: rules file newly named` is a rules file the diff
  may not change: read it whole at head). A path marked `skip: <reason>` goes into `coverage.skipped`
  with that reason; skip nothing else. A test file is never skipped unless the tool marks it so.
- Copy every `# review.paths:` line into the report: it says which config was read and whether
  the project's entries changed in this diff.
- `review-scope: refused: …` or no rigger-core root: use
  `git diff --no-renames --name-status <base>..<head>` as the scope and say so in the report.

## 2. Per-path rules

- After the path lines, each `# rules <glob> <file> (level <n>)` line names a rules file for the
  paths its glob matches. Read each file a `# rules` line names with the Read tool and apply it
  to those paths only.
- Never run git on a path from config or from a `# rules` line: the tool already read the
  project's rules at `<base>` and wrote them to the named file.

## 3. Checklists

The checklists live in `checklists/` at this module's root (two folders above this skill's
folder). Each holds checks no linter or the defect-classes rules cover.

| Checklist | Paths |
|---|---|
| `checklists/python.md` | `.py` |
| `checklists/typescript.md` | `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs` |
| `checklists/php.md` | `.php` |
| `checklists/shell.md` | `.sh`, `.bash`, and a file without an extension whose first line starts `#!` and names `sh` or `bash` |

Load at most 3, in order of how many `review` paths each matches, ties alphabetical. Apply a
checklist to the paths it matches only.

## 4. Review

- Read the diff with `git --no-pager diff --no-ext-diff --no-textconv --no-color <base>..<head>`
  (a large one with `--output=<file in the scratch folder>`), then each `review` path at `<head>`
  where the diff alone does not show the defect.
- A finding's `quote` is copied from the file at `<head>`, and its lines are where the quote is.
- For each change, name every existing caller, hook or user flow that now behaves differently, as
  before → after with `file:line`, under a `Behaviour changes` heading of the report.

## 5. Close

End with the agent's `rigger.reviewer/1` JSON: `coverage.in_scope` is every path the tool printed,
`reviewed` the `review` paths you read, `skipped` the `skip:` paths with their reasons. A path in
scope that is neither reviewed nor skipped makes the status PARTIAL.
