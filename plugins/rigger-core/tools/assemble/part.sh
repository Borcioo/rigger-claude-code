#!/bin/sh
# One rules part handler: sh part.sh <i> [--subagent], with the hook JSON on stdin.
# Runs assemble.py --part <i> through py.sh. The assembler exits 0 with the part's text, or 10 when
# it prints nothing on purpose (resume, a skipped agent type, an empty part). Anything else is a
# failure that would leave the session without rules, and it turns into one visible line (wrapped
# as additionalContext JSON with --subagent): a non-zero status whatever was printed, and exit 0
# with no output (a file created but never filled). Part 1 says the rules are not loaded; a part
# above 1 names itself, so identical-text deduplication cannot hide one lost part behind another.
# No Python 3.9+ is one line from part 1 only, since no part can succeed then. Keep this file
# POSIX (dash). It always exits 0.

# The folder is cut at the last / or \, so a script path written with backslashes still works.
case $0 in
  *[\\/]*) here=${0%[\\/]*} ;;
  *) here=. ;;
esac
root=$here/../..
index=${1-}
mode=${2-}

# --notice on every part: without it py.sh exits 0 with no output when no Python is found, which
# reads as a broken assembler. Parts above 1 then drop its line below.
set -- --notice -S "$root/tools/assemble/assemble.py" --part "$index"
[ "$mode" != --subagent ] || set -- "$@" --subagent

# The trailing "x" keeps every trailing newline of the output, which $(...) would strip.
out=$(sh "$root/bin/py.sh" "$@"; status=$?; printf x; exit $status)
status=$?
out=${out%x}

[ "$status" -ne 10 ] || exit 0

line=
case $out in
  'rigger: Python 3.9+ not found;'*)
    [ "$index" = 1 ] || exit 0
    line='Rigger rules not loaded: Python 3 not found (run /rigger-core:doctor)' ;;
esac
if [ -z "$line" ]; then
  reason=
  if [ "$status" -ne 0 ]; then
    reason="the assembler exited $status"
  elif [ -z "$out" ]; then
    reason='the assembler printed nothing'
  fi
  if [ -z "$reason" ]; then
    printf '%s' "$out"
    exit 0
  fi
  if [ "$index" = 1 ]; then
    line="Rigger rules not loaded: $reason (run /rigger-core:doctor)"
  else
    # The part counts of parts.py, as hooks.json wires them: 6 for a session, 4 for a subagent.
    count=6
    [ "$mode" != --subagent ] || count=4
    line="Rigger rules part $index of $count not loaded: $reason (run /rigger-core:doctor)"
  fi
fi

if [ "$mode" = --subagent ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"%s\\n"}}\n' "$line"
else
  printf '%s\n' "$line"
fi
exit 0
