## State file for long sessions
- **Long sessions keep a state file; they never hand off.** Main session only (subagents never
  write one). From a plan's first task boundary or the context reminder, keep
  `<config dir>/state/<CLAUDE_CODE_SESSION_ID>.md` (`CLAUDE_CONFIG_DIR`, else `~/.claude`):
  `---` / `safe_to_compact: true` / `updated: <ISO time>` / `plan:`, `stop:` when set / `---`,
  then goal, plan file, commits, running agents, `## Next step`, `## Items`, `## Keep verbatim`
  (exact errors, file:line, commands, ids, decisions and why), lessons. Rewrite it at each task
  boundary (commit, green tests, subagent result), refreshing Next step and Keep verbatim,
  with `true`; set `false` before a step that must not be cut (merge, migration).
  Compaction waits for `true`; after it, continue from Next step without
  asking or writing a
  handoff.
- **The state file stays under 9 000 characters,** all that comes back after compaction.
  `## Next step` is the first section: rewrite it, never append to it, at most ~15 lines. History
  goes to `<session id>.log.md`, linked
  from the state file.
- **Change `## Items` only with `<Plugin root>/tools/state/state.py add|set|note|done`**; act
  on stale items named at session start.
