# session-watch — wake the coordinator only when it has something to act on

A `/rigger-core:coordinate` session (step 8) starts it with `run_in_background`:

```sh
sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" --notice "${CLAUDE_PLUGIN_ROOT}/tools/session-watch/session_watch.py"
```

It waits without model tokens, polls every 15 s and exits, which wakes the coordinator. One exit
prints a line for every held note (oldest first) and every quiet session true at that moment, so
read all of them. Python 3.9+, stdlib only; needs the desktop app (`CLAUDE_CODE_HOST_SESSION_ID`).

| Line | When | Exit |
|---|---|---|
| `session-watch: held-note: <name> (<id>) finished a turn at <HH:MM:SS>Z; …` | a note in the coordinator's `queuedSideSessionNotes` is 120 s old | 0 |
| `session-watch: held-note: user-driven: <name> (<id>) finished a turn at <HH:MM:SS>Z the user drove in its window; …` | the same, and the turn that ended within 10 s before the note started with a prompt the user typed in that session (`turnOrigin: human`); the app delivers such a note with the user's next message to the coordinator | 0 |
| `session-watch: quiet: <name> (<id>) mid-turn for <m> min, waiting on <tools> (mode <mode>): …` | a side session is not idle, and neither its last conversation record nor a subagent file changed for 300 s; never for a session in `bypassPermissions` waiting only on `Bash` or `PowerShell` (a running command, nothing can ask) | 0 |
| `session-watch: cap: 30 min; <name> (<state>), …` | 1800 s passed | 0 |
| `session-watch: nothing to watch: no open side session of <id>` | no unarchived side session that is not a fork the user made | 0 |
| `session-watch: error: <what> (<path>)` | a file it cannot read or a shape it does not know (list in the module docstring) | 2 |

## Sources

- Desktop app session files, `<app>/claude-code-sessions/<account>/<org>/local_<id>.json`
  (`<app>`: Windows `%APPDATA%\Claude`; macOS `~/Library/Application Support/Claude`, unverified;
  Linux `$XDG_CONFIG_HOME/Claude` or `~/.config/Claude`; `--app-dir` overrides). Read:
  `queuedSideSessionNotes` of the coordinator; `spawnedFrom.sessionId`, `spawnedFrom.userFork`,
  `spawnedFrom.name`, `isArchived`, `cliSessionId`, `createdAt`, `permissionMode` of each side session.
- Transcripts `<config>/projects/*/<cliSessionId>.jsonl` (`<config>`: `CLAUDE_CONFIG_DIR`, else
  `~/.claude`), read from the end (`turnOrigin` of the prompt that started a held note's turn);
  subagent files in `<cliSessionId>/subagents/`.
- Reported items: `<tempdir>/rigger-session-watch/<id>.json` (`--state-dir` overrides), so a
  restart never reports the same note or quiet state twice.

The app delivers queued notes only once every side session is idle, and never on a turn a
background notice started: a note reported here still arrives later. The watcher reads the app's
queue rather than transcript turn ends because a turn end does not say whether the app has
delivered the note yet.

## Tests

```sh
python -I tools/session-watch/tests/test_session_watch.py
```

Fixtures in `tests/fixtures/` are real transcript lines reduced to the keys the script reads.
