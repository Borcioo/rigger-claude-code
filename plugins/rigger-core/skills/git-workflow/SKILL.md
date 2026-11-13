---
name: git-workflow
description: >-
  Load BEFORE the first git or gh command that writes to a repo (branch, commit, push, merge,
  rebase, tag), even a "just commit and push"; before a pull request, merge conflict, release,
  version bump, changelog or tag; or when reading history to learn why code changed.
---

# Git workflow

Work happens on short-lived branches off the repository's default branch (`main` unless the repo
says otherwise) and lands through a pull request. Never commit directly to the default branch and
never force-push it.

## Branches

- `feature/<slug>`, `fix/<slug>`, `chore/<slug>` start from an up-to-date default branch and live a
  day or two. Lowercase with hyphens: `fix/empty-config-crash`. Agents use these prefixes too, not a
  tool default such as `claude/…`. A repo with its own convention keeps it.
- A repo without `.gitattributes` (`* text=auto eol=lf`): propose it once, before its first commit
  in the session (on Windows with rigger-windows installed, the template and renormalize step are in
  `rigger-windows:windows-dev-env` "Line endings"); if the user declines, record that in the
  project's CLAUDE.md and never propose it again.
- Never commit environment-specific values (`.env`, local config with hosts or keys).
- To bring new default-branch commits into a work branch: `git fetch && git merge origin/<default>`.
  Never rebase or force-push a pushed branch; the squash merge hides the merge commit anyway.
- **A file both sides regenerate (`.po`/`.pot`, generated assets) merges three-way against the last
  common base**: plain `git merge`, or `git merge-file <ours> <base> <theirs>` for one file. Never
  resolve it with `-X ours` or a union script: an ours-first union silently keeps the other side's
  updated strings at their old value.
- A claim that a later merge is clean comes with `git merge-tree <base> <a> <b>` output, and each
  branch's repo-wide tests run against the other's files first: text can merge while a test breaks.
  An "expected failures" claim is a list of test names, never a count.

## Commits

- Conventional Commits in English: `type(scope): subject`, imperative, at most ~72 characters;
  breaking change: `feat!:`. The body says why, not what. One logical change per commit.
- No `Co-Authored-By` or tool-attribution lines.
- Chain `git add`, `git commit` and `git push` with `&&`, never `;`, and read `git status --short`
  before committing: a pathspec that matches nothing (a path already `git rm`'d or never tracked)
  aborts the whole `git add`.
- Trailers are the **last paragraph**, after a blank line, nothing below them: `Refs: #123` when an
  issue exists; `BREAKING-CHANGE: …` when others must adapt. Keep the hyphen: git rejects a space in
  a trailer key, so `BREAKING CHANGE:` makes it skip the whole block and `Refs` lookups find nothing.

## Pull request

The squash commit takes its subject from the PR title and its message from the PR body.

1. Push the branch; `gh pr create --base <default> --title "<type(scope): subject>" --body-file <file>`.
   Title: a valid Conventional Commit. Body: why, and how it was checked (commands run, results),
   as plain paragraphs without headings; trailers as its last paragraph. A user-visible change also
   adds its line under `## [Unreleased]` in `CHANGELOG.md` when the repo keeps one. Stop if
   `gh pr create` fails; take `<n>` only from a URL ending `/pull/<n>`.
2. Gates green (tests, lint) and a separate reviewer agent approved.
3. Only after the user's OK for this PR: `gh pr merge <n> --squash --delete-branch`. Without it,
   stop at a green, reviewed PR and give the user its link.
4. `git switch <default> && git pull --ff-only`; delete a leftover local branch with
   `git branch -D` (`-d` refuses after a squash).

No remote: after the same OK, on the default branch, `git merge --squash <branch>`, then commit
with the PR-style message.

## Release (plugins, libraries, tools)

Needs the user's OK; an explicit request to release is that OK for the commit it names, and
anything merged after it needs a new OK. Versions are SemVer: manifest or header `1.4.2` → tag
`v1.4.2`. The last tag decides the next number
(`git fetch origin --tags && git describe --tags --abbrev=0 origin/<default>`; with no tag yet,
start from the version in the manifest).

1. **Bump the version through a PR** as above, titled `chore(release): <version>`: every copy of the
   old version (`git grep -nF '<old>' -- . ':!vendor' ':!*.lock'`: manifest, version constant,
   `package.json`, `composer.json`, plugin header, readme), and `CHANGELOG.md` with Unreleased
   moved under the new version.
2. Tag its squash commit with an annotated tag and push only that tag. Every release gets a tag.

   ```bash
   git fetch origin --tags
   sha=$(gh pr view <n> --json mergeCommit --jq .mergeCommit.oid)
   git merge-base --is-ancestor "$sha" origin/<default>   # else stop and ask the user
   git tag -a v<version> "$sha" -m "v<version>: <one-line summary>"
   git push origin v<version>
   ```

   No remote: on the default branch, `git tag -a v<version> -m "v<version>: <one-line summary>"`.
   Never move or delete a pushed tag; a wrong one is superseded by the next version.
3. `gh release create v<version> --verify-tag --generate-notes`. Stop if it fails.
4. If a workflow publishes or deploys on a tag push, say so when asking for the OK: pushing the tag
   is then the release itself.

A fix for an older release that the default branch cannot ship yet goes on
`release/<major>.<minor>`, cut from that release's tag: fix, bump the patch version, tag it there,
and bring the fix to the default branch through its own PR in the same session.

## Repo settings

Each repo should have: rebase merge off; squash commit message = PR title and description. If a
setting differs, tell the user in one line instead of changing it. Before planning around required
approvals or status checks, read what the repo actually enforces
(`gh api repos/<owner>/<repo>/rules/branches/<default>`); an empty list or a 403/404 means nothing
is enforced there, so the rules above are the only gate.

## Reading history

| To find | Run |
|---|---|
| Releases | `git tag --sort=-creatordate` or `gh release list` |
| What changed between releases | `git log --no-merges --oneline vA..vB` |
| Linked issues | `git log --format='%h %s %(trailers:key=Refs,valueonly,separator=%x2C )' vA..vB` |
| Why a line changed | `git log -L <start>,<end>:<file>`; the `(#N)` in the subject leads to the PR |
