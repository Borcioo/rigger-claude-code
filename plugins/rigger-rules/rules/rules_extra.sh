#!/bin/sh
# Prints the user's own rules: every *.md in <config dir>/rigger/rules.d/, sorted by name (C order).
#
#   rules_extra.sh             SessionStart: the files as text
#   rules_extra.sh --subagent  SubagentStart: the same text as JSON additionalContext, without the
#                              *-main.md files; prints nothing for the agent types rules_start.sh skips
#
# <config dir> is CLAUDE_CONFIG_DIR, else $HOME/.claude; a missing folder prints nothing. CR and other
# control characters except tab are dropped. Output stops before the file that would take it over
# 9,000 bytes (the hook text cap is 10,000 characters); that file and the rest are named in one line.
# Hook commands reach this through sh, so only POSIX syntax. It always exits 0.

LC_ALL=C
export LC_ALL
cap=9000
newline='
'
tab='	'

mode=text
if [ "${1:-}" = --subagent ]; then
  mode=json
  input=$(cat)
  for agent in Explore Plan rigger-core:implementer rigger-core:mapper; do
    case $input in
      *"\"agent_type\":\"$agent\""* | *"\"agent_type\": \"$agent\""*) exit 0 ;;
    esac
  done
fi

dir=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/rigger/rules.d

# Sets `replaced` to $1 with every $2 replaced by $3; the inserted text is not scanned again.
replace_all() {
  replaced=
  rest=$1
  while :; do
    case $rest in
      *"$2"*)
        replaced=$replaced${rest%%"$2"*}$3
        rest=${rest#*"$2"}
        ;;
      *) break ;;
    esac
  done
  replaced=$replaced$rest
}

# Sets `piece` to $1 plus a newline, JSON-escaped in --subagent mode.
encode() {
  if [ "$mode" = json ]; then
    replace_all "$1" '\' '\\'
    replace_all "$replaced" '"' '\"'
    replace_all "$replaced" "$tab" '\t'
    replace_all "$replaced" "$newline" '\n'
    piece=$replaced'\n'
  else
    piece=$1$newline
  fi
}

out=
size=0
add() {
  encode "$1"
  out=$out$piece
  size=$((size + ${#piece}))
}

left=
for path in "$dir"/*.md; do
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    continue
  fi
  name=${path##*/}
  if [ "$mode" = json ]; then
    case $name in
      *-main.md) continue ;;
    esac
  fi
  if [ -n "$left" ]; then
    left="$left, $name"
    continue
  fi
  if [ ! -f "$path" ] || [ ! -r "$path" ] || ! text=$(tr -d '\000-\010\013-\037' <"$path"); then
    add "rules_extra: cannot read $name"
    continue
  fi
  if [ -z "$text" ]; then
    continue
  fi
  encode "$text"
  if [ $((size + ${#piece})) -gt $cap ]; then
    left=$name
    continue
  fi
  out=$out$piece
  size=$((size + ${#piece}))
done
if [ -n "$left" ]; then
  encode "rules_extra: left out over the hook cap: $left"
  out=$out$piece
fi

if [ -z "$out" ]; then
  exit 0
fi
if [ "$mode" = json ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"%s"}}\n' "$out"
else
  printf '%s' "$out"
fi
exit 0
