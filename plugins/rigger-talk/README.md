# rigger-talk

Opt-in. Adds the reply-format rules from `rules/talk.md` to the main session; subagents do not get
them. rigger-core's assembler prints the file with the options applied, on `startup|clear|compact`.

| Option | Meaning | Default |
|---|---|---|
| `reply_language` | language of replies; empty follows Claude Code's `language` setting | empty |
| `reply_lines` | default reply length in lines | 5 |
| `report_lines` | final-report length in lines | 15 |

Set the options in Claude Code's plugin settings. The assembler reads them from `pluginConfigs` in
`<config dir>/settings.json`: `rigger-talk@<marketplace>` when installed, `rigger-talk@inline` when
loaded with `--plugin-dir` and `RIGGER_MODULE_DIRS`. An unset option, an empty one, or a number that
is not a positive integer gets the default from `plugin.json`. `hooks/talk_start.sh` is run by no
hook and goes in the next release. The `hooks/core_check.sh` hook prints one line when rigger-core
is too old to print these rules.

`HARNESS_RULES_DROP` works here as in `rigger-rules`; its README has the contract.
