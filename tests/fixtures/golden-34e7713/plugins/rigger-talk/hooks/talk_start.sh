#!/bin/sh
# SessionStart hook of rigger-talk: prints rules/talk.md with the userConfig options applied.
# Claude Code exports an option that has a value as CLAUDE_PLUGIN_OPTION_<KEY>; it does not export
# defaults, so they live here as well as in plugin.json. A number that is not a positive integer
# counts as unset.

language=${CLAUDE_PLUGIN_OPTION_REPLY_LANGUAGE:-}
lines=${CLAUDE_PLUGIN_OPTION_REPLY_LINES:-}
report=${CLAUDE_PLUGIN_OPTION_REPORT_LINES:-}

case $language in
  '' | *'
'*) language="the language of Claude Code's \`language\` setting (the user's language when it is unset)" ;;
esac
case $lines in
  '' | *[!0-9]* | 0) lines=5 ;;
esac
case $report in
  '' | *[!0-9]* | 0) report=15 ;;
esac

exec sh "${0%/*}/../rules/rules_start.sh" \
  --var "reply_language=$language" \
  --var "reply_lines=$lines" \
  --var "report_lines=$report" \
  talk.md
