---
description: Create or update this project's .claude/environments.json from what the repo and machine already say
---

Fill `.claude/environments.json` in the project root. Shape: the `rigger-panel` README. Top level:
`version` (1), `project`, optional `repository` (its `https` web address, from `git remote -v`, never an ssh address), optional `mcp` (a list of `"<server>: <what agents use it for>"`, names only), `environments`, one
entry per environment (`local`, `dev`, `prod`, others as the project has them) with any of `url`,
`admin`, `server`, `account`, `role`, `path`, `branch`, `database`, `deploy`, `notes`.

Never write a password, token, key, or a connection string with credentials, in any field. Read
`wp-config.php`, `.env` and similar only for a host, path or database name, and never print their
secrets. An ssh alias stays out of the file: it is per machine (the user's ssh config), not per project.

1. Gather, reading only: the project's `CLAUDE.md`, any section of it about deploys or
   environments first; an existing
   `.claude/environments.json`; `README`; deploy workflows in `.github/workflows`; `git remote -v`
   and branches; local site config (`herd.yml`, `docker-compose`, `.env.example`).
2. Merge into the existing file: keep what is there, add what is missing. Where sources disagree,
   say so and keep the existing value. Do not check a URL or connect to a server to fill a gap.
3. Show what you found in one short table, then ask the user only for what you could not find, in
   one form, so they never pick the fields out of text. Where `mcp__visualize__show_widget` exists,
   load `mcp__visualize__read_me` with the `elicitation` module and render it. Fit the form to this
   project: each field is a question, grouped by environment; a text input for a URL or a path,
   options with `Don't know` and `Other` where the values are known, a textarea for notes whose
   placeholder says no passwords, tokens or keys. No field ever asks for a credential. Without that
   tool, use AskUserQuestion, then one message for the rest. An empty, skipped or `Don't know` field
   stays out of the file; an answer that looks like a secret is dropped and named to the user.
4. Write the file with the Write or Edit tool; a hook then checks it as the panel does, and a reason it
   returns means the panel will refuse the file: fix it and write it again. If `CLAUDE.md` lists the
   environments' hosts or URLs, propose replacing that data with one line pointing here, and keep its
   rules; change it only on the user's OK.
5. With rigger-panel installed, tell the user to press `Refresh` in its Environments pane. Committing
   follows the project's git rules.
