#!/bin/sh
# Prints rules files into a session. The same script ships in every plugin with rules; the copies
# are byte-identical (rigger-rules/tests/test_rules_hooks.py checks).
#
#   rules_start.sh FILE...             SessionStart: "Plugin root: <root>", then each file
#   rules_start.sh --subagent FILE...  SubagentStart: the same text as JSON additionalContext;
#                                      prints nothing for the agent types listed below
#   --var NAME=VALUE                   replaces {{NAME}} in the files (repeatable)
#
# FILE is a basename under <root>/rules/. HARNESS_RULES_DROP is documented in rigger-rules/README.md.
# Hook commands reach this through sh, so only POSIX parameter expansion and no bash syntax. It always
# exits 0: a missing file is reported in the printed text, where the model sees it.

root=${CLAUDE_PLUGIN_ROOT:-}
if [ -z "$root" ]; then
  case $0 in
    */*) root=${0%/*}/.. ;;
    *) root=.. ;;
  esac
fi

mode=text
vars=
newline='
'
tab='	'
cr=$(printf '\r')
blank=" $tab$cr"

while [ $# -gt 0 ]; do
  case $1 in
    --subagent) mode=json; shift ;;
    --var) vars=$vars$2$newline; shift 2 ;;
    *) break ;;
  esac
done

trim() {
  trimmed=$1
  trimmed=${trimmed#"${trimmed%%[!$blank]*}"}
  trimmed=${trimmed%"${trimmed##*[!$blank]}"}
}

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

drop=
if [ -n "${HARNESS_RULES_DROP:-}" ]; then
  set -f
  saved_ifs=$IFS
  IFS=";$newline"
  for entry in $HARNESS_RULES_DROP; do
    trim "$entry"
    if [ -n "$trimmed" ]; then
      drop=$drop$newline$trimmed
    fi
  done
  IFS=$saved_ifs
  set +f
  if [ -n "$drop" ]; then
    drop=$drop$newline
  fi
fi

dropped() {
  case $drop in
    *"$newline$1$newline"*) return 0 ;;
  esac
  return 1
}

if [ "$mode" = json ]; then
  input=$(cat)
  for agent in Explore Plan rigger-core:implementer rigger-core:mapper; do
    case $input in
      *"\"agent_type\":\"$agent\""* | *"\"agent_type\": \"$agent\""*) exit 0 ;;
    esac
  done
fi

json_text=

json_escape() {
  replace_all "$1" '\' '\\'
  replace_all "$replaced" '"' '\"'
  replace_all "$replaced" "$tab" '\t'
}

emit() {
  if [ "$mode" = json ]; then
    json_escape "$1"
    json_text=$json_text$replaced'\n'
  else
    printf '%s\n' "$1"
  fi
}

apply_vars() {
  applied=$1
  case $applied in
    *'{{'*) ;;
    *) return ;;
  esac
  set -f
  saved_ifs=$IFS
  IFS=$newline
  for pair in $vars; do
    replace_all "$applied" "{{${pair%%=*}}}" "${pair#*=}"
    applied=$replaced
  done
  IFS=$saved_ifs
  set +f
}

print_lines() {
  skipping=
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%"$cr"}
    case $line in
      '## '*)
        trim "$line"
        if dropped "$trimmed"; then skipping=1; else skipping=; fi
        ;;
    esac
    if [ -z "$skipping" ]; then
      apply_vars "$line"
      emit "$applied"
    fi
  done <"$1"
}

printed_root=
for name in "$@"; do
  if dropped "$name"; then
    continue
  fi
  if [ -z "$printed_root" ]; then
    emit "Plugin root: $root"
    printed_root=1
  fi
  path=$root/rules/$name
  if [ ! -f "$path" ]; then
    emit "rules_start: missing rules file $path"
  else
    print_lines "$path"
  fi
done

if [ "$mode" = json ] && [ -n "$json_text" ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"%s"}}\n' "$json_text"
fi
exit 0
