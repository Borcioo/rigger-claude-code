#!/bin/sh
# PreToolUse launcher: execs the shellwise binary named on the one line of
# $CLAUDE_PLUGIN_DATA/shellwise/current (written by tools/install at session start) as
# `hook claude-code --config <plugin root>/shellwise.toml`, with the hook's stdin. The binary's stdout
# is the decision and it exits 0. No data folder, no `current`, or a line that names no executable
# file: exit 0 with no output, so the command runs unguarded (exit 2 would deny every call).
# Keep this file POSIX (dash, bash 3.2, busybox) and free of awk.

current="${CLAUDE_PLUGIN_DATA-}/shellwise/current"
[ -f "$current" ] && [ -r "$current" ] || exit 0
binary=
IFS= read -r binary <"$current" || :   # a last line without a newline still sets binary
cr=$(printf '\r')
binary=${binary%"$cr"}
[ -f "$binary" ] && [ -x "$binary" ] || exit 0

case $0 in
  */*) here=${0%/*} ;;
  *) here=. ;;
esac
exec "$binary" hook claude-code --config "$here/../shellwise.toml"
