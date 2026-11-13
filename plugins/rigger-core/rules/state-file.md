## State file for long sessions
Only the session the user drives as orchestrator adds `coordinator: true` to the frontmatter; a side session never does.
- **Long sessions keep a state file; they never hand off.** Main session only (subagents never
  write one). From the first task boundary of a plan, or at the context-guard reminder, keep
  `<config dir>/state/<CLAUDE_CODE_SESSION_ID>.md` (config dir: the environment variable
  `CLAUDE_CONFIG_DIR`, else `~/.claude`; the reminder and the post-compaction context name
  it): `---` / `safe_to_compact: true` / `updated: <ISO time>` / `---`, then goal, plan file, tasks
  done, commits, running agents, `## Next step`, `## Keep verbatim` (exact error text, file:line,
  commands, ids, decisions with their reason), lessons. Rewrite it at each task boundary (a commit,
  green tests, a subagent result), refreshing Next step and Keep verbatim, with `true`; set `false`
  before a step that must not be cut (a merge, a multi-file edit, a migration). Compaction waits
  for `true` (up to a hard cap); after it, continue from Next step without asking or writing a
  handoff.
  A coordinator rewrites the file with `true` before every turn end in which it waits for
  a side session or an agent, and sets `false` only around such a step, back to `true` right after.
- **The state file stays under 9 000 characters,** all that comes back after compaction.
  `## Next step` is the first section: rewrite it, never append to it, at most ~15 lines. History
  (results per session, tallies, lifted orders) goes to `<session id>.log.md` next to it, linked
  from the state file.
