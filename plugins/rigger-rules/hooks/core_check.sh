#!/bin/sh
# SessionStart hook of rigger-rules: one visible line when the installed rigger-core has no assembler.
# rigger-core prints every rules section; `claude plugin update rigger-rules@<marketplace>` alone can
# leave an older rigger-core in the cache, and the session would then start with no rules at all.
#
# It reads <config dir>/plugins/installed_plugins.json and checks only when this plugin runs from one
# of its installPath folders (a cache install). A directory marketplace runs hooks from its source
# while installPath is a stale snapshot, so there a current core would read as old: no check. Every
# installPath whose last or second-to-last path element is rigger-core counts as a core; the line
# prints when at least one exists and none holds tools/assemble/part.sh. A missing or unreadable
# file prints nothing. Keep this file POSIX (dash); it needs no Python. It always exits 0.

here=${0%/*}
[ "$here" != "$0" ] || here=.
own=$(cd "$here/.." 2>/dev/null && pwd -P) || exit 0

# A drive-letter path with backslashes (C:\Users\...) reaches sh with forward slashes.
posix() {
  case $1 in
    [A-Za-z]:\\*) printf '%s\n' "$1" | tr '\\' '/' ;;
    *) printf '%s\n' "$1" ;;
  esac
}

config=${CLAUDE_CONFIG_DIR:-}
[ -n "$config" ] || config=${HOME:-}/.claude
config=$(posix "$config")
list=$config/plugins/installed_plugins.json
[ -f "$list" ] || exit 0

# Every "installPath" string value, one per line, with the JSON escapes \\ \/ \" undone. A value
# holding any other escape is skipped. The file may be indented or on one line.
paths=$(awk '
  { text = text $0 "\n" }
  END {
    key = "\"installPath\""
    while ((at = index(text, key)) > 0) {
      text = substr(text, at + length(key))
      sub(/^[ \t\r\n]*:[ \t\r\n]*/, "", text)
      if (substr(text, 1, 1) != "\"") continue
      text = substr(text, 2)
      value = ""
      closed = 0
      while (length(text) > 0) {
        c = substr(text, 1, 1)
        if (c == "\"") { closed = 1; text = substr(text, 2); break }
        if (c == "\\") {
          c = substr(text, 2, 1)
          if (c != "\\" && c != "/" && c != "\"") break
          text = substr(text, 3)
        } else {
          text = substr(text, 2)
        }
        value = value c
      }
      if (closed && value != "") print value
    }
  }
' "$list" 2>/dev/null) || exit 0

ours=no
cores=0
current=no
while IFS= read -r path; do
  [ -n "$path" ] || continue
  path=$(posix "$path")
  path=${path%/}
  last=${path##*/}
  parent=${path%/*}
  if [ "$last" = rigger-core ] || [ "${parent##*/}" = rigger-core ]; then
    cores=$((cores + 1))
    [ ! -f "$path/tools/assemble/part.sh" ] || current=yes
  fi
  real=$(cd "$path" 2>/dev/null && pwd -P) || continue
  [ "$real" != "$own" ] || ours=yes
done <<EOF
$paths
EOF

if [ "$ours" = yes ] && [ "$cores" -gt 0 ] && [ "$current" = no ]; then
  printf '%s\n' 'Rigger rules not loaded: rigger-core is older than rigger-rules (run claude plugin update for rigger-core)'
fi
exit 0
