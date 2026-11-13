## Scope of changes

- Every changed line traces to the request: minimum code for the task, no unrequested features,
  options or abstractions for a single use, no reformatting or renaming of adjacent code; removing
  a container keeps everything else that renders inside it; mention unrelated bugs or dead code
  instead of fixing them in passing.
- Remove only what your own change made unused.
- Content handed to you (copy, data, translations) goes in as given, notes and gaps included; only
  technical repair (encoding, format) is yours. Where a remark is ambiguous, do the unambiguous part
  and ask one concrete question — never speculative variants, edits to neighbouring pages, or
  offers to re-check the author's own facts.
- One OK covers the whole request on the same target environment and the same kind of change
  (content, settings, code): do its later points and report them instead of asking again; a
  merge into the default branch or a deploy still needs its own OK (the Git rule). A request that
  turns out to need another kind (content that needs a code release) gets one line naming the
  cheapest route before you start. Word questions by the goal (what the user of the product gets),
  not by the implementation.
- Write for the reader who acts: skills, CLAUDE.md, docs and code comments hold only what changes
  what the reader does or how they read the code. Sources, history, model names and "we decided"
  go in the PR or commit body. Keep a "because…" only where it stops someone from breaking the rule.
- Tests these rules require for the changed code are part of the task.
- A process guard inside the repo (test, lint rule, helper, template) lands as its own PR (its own
  commit only without a remote) and is listed in the final report.
- A CI or deploy-workflow change, a hook, settings.json, CLAUDE.md, a rules file or a skill is proposed in one
  line with the exact change and waits for my OK.
- Tidy-ups and repo hygiene (`.gitattributes`, renormalize, moves) are proposed first; after my OK
  they land as their own PR (own commit only without a remote), never inside the current change.
