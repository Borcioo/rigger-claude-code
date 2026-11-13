# Moving a repo to another path

`~/.claude.json` keys local-scope MCP servers and folder trust by the absolute project path, so a
move silently drops both. Copy first, delete last: list `projects["<old path>"]` to see what is
keyed there, copy the repo to the new path, re-register each local MCP server with
`claude mcp add -s local` from the new folder, check with `claude mcp get`, remove the old entries
with `claude mcp remove -s local` from the old folder, and only then delete the original. Session
history is keyed the same way and stays behind under `~/.claude/projects/<old encoded path>` —
`--continue` in the new folder starts empty.
