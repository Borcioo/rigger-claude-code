#!/bin/sh
# Run a plugin's Python script with the first Python 3.9+ found.
# Usage: sh bin/py.sh [--notice] <script.py> [args...]
# The interpreter path is cached in $CLAUDE_PLUGIN_DATA/python-path.v2. "none <epoch> <session id>"
# = not found, valid for 600 s in the session that wrote it (a resumed session keeps its id, so a
# Python installed meanwhile is found within ten minutes; none is written without
# CLAUDE_CODE_SESSION_ID); a path never expires, so a changed search order needs a new file name. No
# interpreter: exit 0, and with --notice print one line naming the plugin.
# Keep this file POSIX (dash) and byte-identical in every plugin.

notice=
if [ "${1-}" = "--notice" ]; then
  notice=1
  shift
fi

cache=
if [ -n "${CLAUDE_PLUGIN_DATA-}" ]; then
  cache="$CLAUDE_PLUGIN_DATA/python-path.v2"
fi
py=

# Succeeds when "$@" is Python 3.9+; sets found to its sys.executable.
# stdin is /dev/null so the hook's JSON input stays unread for the script. UTF-8 mode: on Windows
# the default code page would turn a non-ASCII profile folder into a path exec cannot find.
probe() {
  found=$(PYTHONUTF8=1 "$@" -c 'import sys; print(sys.executable); sys.exit(0 if sys.version_info >= (3, 9) else 1)' </dev/null 2>/dev/null) || return 1
  [ -n "$found" ]
}

# Sets py from a valid cache entry: an executable path, or "none" of this session within 600 s.
read_cache() {
  [ -n "$cache" ] && [ -f "$cache" ] || return 1
  entry=
  IFS= read -r entry <"$cache"
  case $entry in
    'none '*)
      rest=${entry#none }
      stamp=${rest%% *}
      case $stamp in '' | *[!0-9]*) return 1 ;; esac
      [ -n "${CLAUDE_CODE_SESSION_ID-}" ] && [ "$rest" = "$stamp $CLAUDE_CODE_SESSION_ID" ] || return 1
      now=$(date +%s)
      age=$((now - stamp))
      [ "$age" -ge 0 ] && [ "$age" -lt 600 ] || return 1
      py=none
      ;;
    *)
      [ -x "$entry" ] || return 1
      py=$entry
      ;;
  esac
}

# Writes the cache entry atomically (parallel hooks share the file).
write_cache() {
  mkdir -p "$CLAUDE_PLUGIN_DATA" 2>/dev/null || return 0
  tmp="$cache.tmp.$$"
  if printf '%s\n' "$1" >"$tmp" 2>/dev/null; then
    mv -f "$tmp" "$cache" 2>/dev/null
  fi
  return 0
}

search() {
  if [ "${OS-}" = "Windows_NT" ]; then
    # py first: the python on PATH may be the Store build, which starts about twice as slowly.
    probe py -3 || probe python || probe python3
    return
  fi
  # On macOS /usr/bin/python3 is a shim that opens the Command Line Tools installer.
  use_path_python3=1
  if [ "$(command -v python3 2>/dev/null)" = /usr/bin/python3 ] && [ -x /usr/bin/xcode-select ] &&
    ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
    use_path_python3=
  fi
  { [ -n "$use_path_python3" ] && probe python3; } ||
    probe /opt/homebrew/bin/python3 ||
    probe /usr/local/bin/python3 ||
    probe python
}

if ! read_cache; then
  if search; then
    py=$found
    [ -z "$cache" ] || write_cache "$py"
  else
    py=none
    if [ -n "$cache" ] && [ -n "${CLAUDE_CODE_SESSION_ID-}" ]; then
      write_cache "none $(date +%s) $CLAUDE_CODE_SESSION_ID"
    fi
  fi
fi

if [ "$py" = none ]; then
  if [ -n "$notice" ]; then
    plugin=plugin
    here=${0%/*}
    [ "$here" != "$0" ] || here=.
    manifest="$here/../.claude-plugin/plugin.json"
    if [ -f "$manifest" ]; then
      while IFS= read -r line; do
        case $line in
          *'"name"'*)
            line=${line#*'"name"'}
            line=${line#*\"}
            plugin=${line%%\"*}
            break
            ;;
        esac
      done <"$manifest"
    fi
    printf 'rigger: Python 3.9+ not found; %s hooks are off\n' "$plugin"
  fi
  exit 0
fi

exec "$py" -I -X utf8 "$@"
