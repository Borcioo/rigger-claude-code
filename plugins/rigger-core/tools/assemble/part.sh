#!/bin/sh
# One rules part handler: sh part.sh <i> [--subagent], with the hook JSON on stdin.
# Runs assemble.py --part <i> through py.sh and prints its output unchanged. Part 1 turns the two
# failures that would leave the session without rules into one visible line (wrapped as
# additionalContext JSON with --subagent): no Python 3.9+, and an interpreter that exited non-zero
# with nothing on stdout (a syntax error, a missing module, assemble.py gone). Parts 2..K print
# nothing then. Keep this file POSIX (dash). It always exits 0.

here=${0%/*}
[ "$here" != "$0" ] || here=.
root=$here/../..
index=${1-}
mode=${2-}

if [ "$index" = 1 ]; then
  set -- --notice
else
  set --
fi
set -- "$@" -S "$root/tools/assemble/assemble.py" --part "$index"
[ "$mode" != --subagent ] || set -- "$@" --subagent

# The trailing "x" keeps every trailing newline of the output, which $(...) would strip.
out=$(sh "$root/bin/py.sh" "$@"; status=$?; printf x; exit $status)
status=$?
out=${out%x}

line=
case $out in
  'rigger: Python 3.9+ not found;'*) line='Rigger rules not loaded: Python 3 not found (run /rigger-core:doctor)' ;;
  '') [ "$status" -eq 0 ] || line="Rigger rules not loaded: the assembler exited $status (run /rigger-core:doctor)" ;;
esac

if [ -z "$line" ]; then
  printf '%s' "$out"
  exit 0
fi
[ "$index" = 1 ] || exit 0
if [ "$mode" = --subagent ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"%s\\n"}}\n' "$line"
else
  printf '%s\n' "$line"
fi
exit 0
