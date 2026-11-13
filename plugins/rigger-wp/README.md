# rigger-wp

Opt-in, for WordPress developers. The rigger-wp plugin provides two skills. `rigger-wp-traps` holds the WordPress traps: the mistakes that recur when writing plugin and theme code, integration tests, migrations, roles and asset enqueues. Claude loads it before working on WordPress code so the checks happen while the code is written rather than after a review finds the problem. `wordpress-router` is the one listed entry for the upstream WordPress skills (below).

It injects the rule that says when to load each skill, on startup, clear and compact.

## Upstream WordPress skills through the router

The skills of the upstream WordPress agent-skills repository (`https://github.com/WordPress/agent-skills`,
GPL-2.0-or-later, Copyright (C) WordPress Contributors) are vendored unmodified under
`skills/wordpress-router/references/skills/`, with the licence notice and the upstream commit in
`skills/wordpress-router/references/` (`LICENSE`, `UPSTREAM.md`). They are not listed at session start:
only the router is. It indexes them and the model Reads the ones the task needs. The router `SKILL.md`
and `tools/sync_wp_skills.py` are Rigger's own; nothing under `references/skills/` is edited by hand.

`tools/sync_wp_skills.py` refreshes the copy. It is a dry run until `--apply`: it prints the file summary
and diffs, lists skills upstream removed, rewrites the router index and `UPSTREAM.md`, and refuses an
empty or non-GPL upstream. Run it by hand or from a scheduled job, review the diff as a commit, and release
it through the marketplace; nothing changes what loads without that commit.

    python -I plugins/rigger-wp/tools/sync_wp_skills.py            # dry run, exit 10 when changes are pending
    python -I plugins/rigger-wp/tools/sync_wp_skills.py --apply
