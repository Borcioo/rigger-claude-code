#!/bin/sh
# One rules part handler: sh part.sh <i> [--subagent], with the hook JSON on stdin.
# Runs assemble.py --part <i> through py.sh. The assembler exits 0 with the part's text, or 10 when
# it prints nothing on purpose (resume, a skipped agent type, an empty part). Anything else is a
# failure that would leave the session without rules, and part 1 turns it into one visible line
# (wrapped as additionalContext JSON with --subagent): no Python 3.9+, a non-zero status whatever
# was printed, and exit 0 with no output (a file created but never filled). Parts 2..K print
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

[ "$status" -ne 10 ] || exit 0

line=
case $out in
  'rigger: Python 3.9+ not found;'*) line='Rigger rules not loaded: Python 3 not found (run /rigger-core:doctor)' ;;
esac
if [ -z "$line" ]; then
  if [ "$status" -ne 0 ]; then
    line="Rigger rules not loaded: the assembler exited $status (run /rigger-core:doctor)"
  elif [ -z "$out" ]; then
    line='Rigger rules not loaded: the assembler printed nothing (run /rigger-core:doctor)'
  fi
fi

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
