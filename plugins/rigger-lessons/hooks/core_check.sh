#!/bin/sh
# SessionStart hook of every plugin whose rules rigger-core prints: one visible line when the installed
# rigger-core is older than the contract below. `claude plugin update <plugin>@<marketplace>` alone can leave an older
# rigger-core in the cache, and that plugin's rules would then be missing with no word about it.
# Usage: sh core_check.sh <plugin name>; no name prints nothing. The same file ships in each of those
# plugins (tests/test_repo.py checks the copies are equal); the name keeps their hooks.json commands
# and their lines apart.
#
# Contract with rigger-core across releases: a core is current when it holds rules/skills.md, the shared
# Skill triggers table every module trigger row targets (since 0.6.0). A 0.5.0 core already holds
# tools/assemble/part.sh but not that table, so the modules' trigger rows would vanish with no notice. Move
# the probe to a newer core file whenever these plugins start to need one.
#
# It reads <config dir>/plugins/installed_plugins.json and checks only when this plugin runs from one
# of its own installPath folders (a cache install), compared after pwd -P on both. A directory
# marketplace runs hooks from its source while installPath is a stale snapshot, so there a current core
# would read as old: no check. Every installPath whose last or second-to-last path element is
# rigger-core counts as a core; the line prints when at least one exists and none holds rules/skills.md. A
# missing or unreadable file prints nothing. Known gaps: a current core loaded with --plugin-dir next
# to an old cached core still prints the line, and when two scopes list rigger-core any current entry
# keeps it silent, whichever one the session loaded. Keep this file POSIX (dash); it needs no Python.
# It always exits 0.

name=${1:-}
[ -n "$name" ] || exit 0

# The folder of this script, cut at the last / or \: a Windows host may pass the path with backslashes.
case $0 in *[\\/]*) here=${0%[\\/]*} ;; *) here=. ;; esac
own=$(cd "$here/.." 2>/dev/null && pwd -P) || exit 0

config=${CLAUDE_CONFIG_DIR:-}
[ -n "$config" ] || config=${HOME:-}/.claude
list=$config/plugins/installed_plugins.json
[ -f "$list" ] || exit 0

# One line per installPath of rigger-core or of this plugin, "core <path>" or "mine <path>", by its last
# or second-to-last path element: the other plugins cost no process here. The file is one record (JSON
# holds no raw \001) and one split() on the key keeps the scan linear in the file size; joining lines
# instead is quadratic in mawk and busybox awk. The JSON escapes \\ \/ \" are undone (a value holding
# any other escape is skipped) and a drive-letter path gets forward slashes. The file may be indented or
# on one line.
paths=$(awk -v name="$name" '
  BEGIN { RS = "\001" }
  {
    count = split($0, chunk, /"installPath"[ \t\r\n]*:[ \t\r\n]*"/)
    for (i = 2; i <= count; i++) {
      rest = chunk[i]
      size = length(rest)
      value = ""
      closed = 0
      for (at = 1; at <= size; at++) {
        c = substr(rest, at, 1)
        if (c == "\"") { closed = 1; break }
        if (c == "\\") {
          at++
          c = substr(rest, at, 1)
          if (c != "\\" && c != "/" && c != "\"") break
        }
        value = value c
      }
      if (!closed || value == "") continue
      if (value ~ /^[A-Za-z]:\\/) gsub(/\\/, "/", value)
      sub(/\/+$/, "", value)
      n = split(value, parts, "/")
      if (parts[n] == "rigger-core" || (n > 1 && parts[n - 1] == "rigger-core")) print "core " value
      else if (parts[n] == name || (n > 1 && parts[n - 1] == name)) print "mine " value
    }
  }
' "$list" 2>/dev/null) || exit 0

ours=no
cores=0
current=no
while read -r kind path; do
  case $kind in
    core)
      cores=$((cores + 1))
      [ ! -f "$path/rules/skills.md" ] || current=yes
      ;;
    mine)
      real=$(cd "$path" 2>/dev/null && pwd -P) || continue
      [ "$real" != "$own" ] || ours=yes
      ;;
  esac
done <<EOF
$paths
EOF

if [ "$ours" = yes ] && [ "$cores" -gt 0 ] && [ "$current" = no ]; then
  printf '%s\n' "Rigger rules not loaded for $name: rigger-core is older than $name (run claude plugin update rigger-core)"
fi
exit 0
