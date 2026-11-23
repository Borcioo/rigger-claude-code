## Working environment

- When something is out of place (loose files, temp output in a repo, a stale worktree, the same
  thing in two places), mention it once at the end of the task in one line and propose the move;
  never move or delete my files without asking. Clean up your own worktrees and scratch once merged;
  a sign-in, dev server or temp account I can see stays until the task ends and is listed in the
  final report.
- In the Claude desktop app, a side session is a session you started from this one for a part of
  the work. When one has its result checked and relayed, archive it in that same turn, unless it
  holds a decision, review, report or discussion I would open it for; archived transcripts stay for
  audits. With rigger-core installed, its side-session guard refuses a `wrap_up` call or the end of
  the turn once per finished result while such a session is open and not listed in the state file
  (rigger-core's "State file for long sessions" rule): archive it, or add
  `<session id>: <why it stays open>` under `## Side sessions kept` in that file.
- MCP servers and plugins: `local` scope (`-s local`, never `project`) unless most sessions use
  them (`claude mcp add-json <name> '<json>'`, `claude plugin enable <id> --scope local`, from the
  project folder). Any server, plugin or integration counts as installed only after one real call
  returns a known answer.
- Before any `ssh`, `scp`, `rsync` or deploy to a project's server, read `.claude/environments.json`
  in the project root (`/rigger-core:environments` creates or updates it). When you learn a fact
  about an environment (URL, admin, server, account, role, path, branch, database name, deploy
  step), write it into that file. Never write a password, token, key or a connection string with
  credentials, in any field.
