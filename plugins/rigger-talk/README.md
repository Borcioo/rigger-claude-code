# rigger-talk

Opt-in. Adds the reply-format rules from `rules/talk.md` to the main session; subagents do not get
them. A `SessionStart` hook on `startup|clear|compact` prints the file with the options applied.

| Option | Meaning | Default |
|---|---|---|
| `reply_language` | language of replies; empty follows Claude Code's `language` setting | empty |
| `reply_lines` | default reply length in lines | 5 |
| `report_lines` | final-report length in lines | 15 |

The hook reads each option from `CLAUDE_PLUGIN_OPTION_<KEY>`. Claude Code does not export defaults,
so `hooks/talk_start.sh` applies them itself, also to a number that is not a positive integer. Keep
its defaults equal to those in `plugin.json`; a test compares them.

`HARNESS_RULES_DROP` works here as in `rigger-rules`; its README has the contract.
