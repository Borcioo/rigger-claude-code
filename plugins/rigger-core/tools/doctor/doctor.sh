#!/bin/sh
# Read-only checks of what the Rigger plugins need on this machine, for /rigger-core:doctor.
# Prints one tab-separated line per row:
#   CHECK <name> OK|MISSING|WARN|INFO <detail> <how checked>
#   FIX <name> <command>    (only after a MISSING or WARN row of that name)
# Exit 0 whatever it finds; exit 2 with one reason line on stderr when it cannot check.
# POSIX sh (dash, Git Bash) with uname, tr and awk; it never writes a file and never runs claude, which
# writes the config folder. DOCTOR_FS_ROOT (tests only) prefixes /etc/os-release, /usr/bin/git and the
# brew paths.
set -u
set -f

for tool in uname tr awk; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf 'doctor.sh: %s not found on PATH\n' "$tool" >&2
    exit 2
  fi
done
if [ -n "${CLAUDE_CONFIG_DIR-}" ]; then
  config=$CLAUDE_CONFIG_DIR
elif [ -n "${HOME-}" ]; then
  config=$HOME/.claude
else
  printf 'doctor.sh: neither CLAUDE_CONFIG_DIR nor HOME is set\n' >&2
  exit 2
fi
fs=${DOCTOR_FS_ROOT-}
# Split on both separators: Windows callers pass "<root>\tools\doctor\doctor.sh".
here=${0%[/\\]*}
[ "$here" != "$0" ] || here=.
root=$here/../..
if [ ! -f "$root/bin/py.sh" ]; then
  printf 'doctor.sh: %s not found, so the python check cannot run\n' "$root/bin/py.sh" >&2
  exit 2
fi
nl='
'

check() { printf 'CHECK\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4"; }
fix() { printf 'FIX\t%s\t%s\n' "$1" "$2"; }

# Under Git Bash every process costs tens of milliseconds, so children run without pipes and the three JSON
# files are read by one awk run.

# Sets line to the first line of $1 without CR bytes (a Windows program writes CRLF).
first_line() {
  line=${1%%"$nl"*}
  while :; do
    case $line in
      *"$cr"*) line=${line%%"$cr"*}${line#*"$cr"} ;;
      *) return 0 ;;
    esac
  done
}

# Sets trimmed to $1 without the trailing newlines $(...) drops, and with crlf set the CR before each, as
# Git Bash drops them.
trim() {
  trimmed=$1
  while :; do
    case $trimmed in
      *"$nl")
        trimmed=${trimmed%"$nl"}
        [ -z "$crlf" ] || trimmed=${trimmed%"$cr"}
        ;;
      *) return 0 ;;
    esac
  done
}

# Reads the JSON files $1 $2 $3 ("" = missing) with the EREs $4 $5 $6. Prints a line "<CR><TAB>", then shell
# assignments: j<i> holds "<path><TAB><type><TAB><value><LF>" for each value whose path (/key/0/key) matches
# the ERE; type s = string (unescaped), n = number or literal, o = object, a = array. s<i> is 0, or 3 when the
# file is not valid JSON (j<i> is then empty) and 2 when it cannot be read. Last "x<CR><LF>": $(...) leaves
# "x" where it drops a CR before a trailing newline. Pretty-printed or one line, CRLF or a BOM read the same; a
# repeated key keeps its last value, as JSON.parse does. A "/" in a key is stored as \034, so such a key never
# matches a lookup path.
json() {
  LC_ALL=C awk -v k1="$4" -v k2="$5" -v k3="$6" '
    function ws() { while (pos <= n && index(" \t\n\r", substr(s, pos, 1))) pos++ }
    function emit(p, t, v,   i) {
      for (i = 1; i <= lines; i++) if (at[i] == p || index(at[i], p "/") == 1) at[i] = ""
      if (p ~ keep) { at[++lines] = p; out[lines] = p "\t" t "\t" v }
    }
    function str(   c, v) {
      v = ""
      for (pos++; pos <= n; pos++) {
        c = substr(s, pos, 1)
        if (c == "\"") { pos++; return v }
        if (c == "\n" || c == "\r") break
        if (c == "\\") {
          c = substr(s, ++pos, 1)
          if (c == "n" || c == "t" || c == "r") c = " "
          else if (c == "u") c = "\\u"
        }
        v = v c
      }
      bad = 1
      return v
    }
    function value(p,   c, k, i) {
      ws()
      c = substr(s, pos, 1)
      if (c == "{") {
        emit(p, "o", "")
        pos++; ws()
        if (substr(s, pos, 1) == "}") { pos++; return }
        while (!bad) {
          ws()
          if (substr(s, pos, 1) != "\"") { bad = 1; return }
          k = str(); ws()
          if (substr(s, pos, 1) != ":") { bad = 1; return }
          gsub("/", "\034", k)
          pos++
          value(p "/" k)
          ws(); c = substr(s, pos++, 1)
          if (c == "}") return
          if (c != ",") bad = 1
        }
        return
      }
      if (c == "[") {
        emit(p, "a", "")
        pos++; ws()
        if (substr(s, pos, 1) == "]") { pos++; return }
        for (i = 0; !bad; i++) {
          value(p "/" i)
          ws(); c = substr(s, pos++, 1)
          if (c == "]") return
          if (c != ",") bad = 1
        }
        return
      }
      if (c == "\"") { emit(p, "s", str()); return }
      k = ""
      while (pos <= n && !index(",:{}[] \t\n\r\"", substr(s, pos, 1))) k = k substr(s, pos++, 1)
      if (k == "") bad = 1
      else emit(p, "n", k)
    }
    function swap(v, a, b,   r, i) {
      r = ""
      while ((i = index(v, a)) > 0) { r = r substr(v, 1, i - 1) b; v = substr(v, i + 1) }
      return r v
    }
    # Git Bash drops a CR from the text eval parses, so a CR is spelled "$cr".
    function quote(v) { return "\047" swap(swap(v, "\047", "\047\\\047\047"), "\r", "\047\"$cr\"\047") "\047" }
    BEGIN {
      printf "\r\t\n"
      for (f = 1; f <= 3; f++) {
        file = ARGV[f]; rows = ""; status = 0
        if (file != "") {
          keep = f == 1 ? k1 : f == 2 ? k2 : k3
          s = ""; bad = 0; lines = 0
          while ((got = (getline text < file)) > 0) s = s text "\n"
          close(file)
          if (got < 0) status = 2
          else {
            if (substr(s, 1, 3) == "\357\273\277") s = substr(s, 4)
            n = length(s); pos = 1
            value("")
            ws()
            if (bad || pos <= n) status = 3
            else for (i = 1; i <= lines; i++) if (at[i] != "") rows = rows out[i] "\n"
          }
        }
        print "j" f "=" quote(rows)
        print "s" f "=" status
      }
      printf "x\r\n"
      exit 0
    }' "$1" "$2" "$3" 2>/dev/null
}

# Sets jtype and jval from the line for path $2 in json output $1; returns 1 when there is none.
get() {
  jtype=''
  jval=''
  case "$nl$1" in
    *"$nl$2$tab"*) ;;
    *) return 1 ;;
  esac
  jval=$nl$1
  jval=${jval#*"$nl$2$tab"}
  jval=${jval%%"$nl"*}
  jtype=${jval%%"$tab"*}
  jval=${jval#*"$tab"}
}

# Sets ver to the version of the first user-scope install of plugin key $1, else other to the first
# project or local install ("<project> (<scope> scope)").
plugin_state() {
  ver=''
  other=''
  get "$installed" "/plugins/$1" || return 0
  case $jtype in
    o) install_entry "/plugins/$1" ;;
    a)
      i=0
      while [ -z "$ver" ] && get "$installed" "/plugins/$1/$i"; do
        install_entry "/plugins/$1/$i"
        i=$((i + 1))
      done
      ;;
  esac
}

install_entry() {
  get "$installed" "$1/scope"
  scope=$jval
  get "$installed" "$1/version"
  [ "$jtype" = s ] || jval=
  if [ "$scope" = user ]; then
    ver=$jval
  elif [ -z "$other" ] && [ -n "$scope" ]; then
    get "$installed" "$1/projectPath"
    other="${jval:-an unnamed project} ($scope scope)"
  fi
}

# Row $1 for plugin key $2, status $3 when it is not installed; returns 1 then, for the caller's FIX.
plugin_row() {
  if [ -n "$plugins_bad" ]; then
    check "$1" WARN "unknown: $plugins_bad is not valid JSON" "$how_plugins"
    return 0
  fi
  plugin_state "$2"
  if [ -z "$ver" ]; then
    if [ -n "$other" ]; then
      check "$1" WARN "installed only for $other" "$how_plugins"
    else
      check "$1" "$3" "not installed" "$how_plugins"
    fi
    return 1
  fi
  on=
  if get "$settings" "/enabledPlugins/$2" && [ "$jtype" = n ]; then on=$jval; fi
  case $on in
    true) check "$1" OK "$ver" "$how_plugins" ;;
    false)
      check "$1" WARN "$ver, disabled" "$how_plugins"
      fix "$1" "claude plugin enable $2"
      ;;
    *)
      check "$1" WARN "$ver, not in enabledPlugins" "$how_plugins"
      fix "$1" "claude plugin enable $2"
      ;;
  esac
}

# Sets cmd to the command that installs package $1 (git or python3) on this Linux distribution.
linux_install() {
  for id in $distro; do
    case $id in
      debian | ubuntu) cmd="sudo apt install -y $1" ;;
      fedora | rhel | centos) cmd="sudo dnf install -y $1" ;;
      arch)
        if [ "$1" = python3 ]; then cmd='sudo pacman -S --noconfirm python'; else cmd="sudo pacman -S --noconfirm $1"; fi
        ;;
      opensuse* | suse) cmd="sudo zypper install -y $1" ;;
      alpine) cmd="sudo apk add $1" ;;
      *) continue ;;
    esac
    return 0
  done
  cmd="install $1 with your package manager"
}

how_settings="$config/settings.json"
how_installed="$config/plugins/installed_plugins.json"
how_plugins="$how_installed, $how_settings"
how_markets="$config/plugins/known_marketplaces.json"
f1=
f2=
f3=
[ ! -f "$how_settings" ] || f1=$how_settings
[ ! -f "$how_installed" ] || f2=$how_installed
[ ! -f "$how_markets" ] || f3=$how_markets
# First, because tab and cr come from this run (printf would cost a process each, as when awk fails).
out=$(json "$f1" "$f2" "$f3" \
  '^/(enabledPlugins(/[^/]*)?|autoCompactWindow|env/(CLAUDE_CODE_USE_POWERSHELL_TOOL|HARNESS_WORKTREE_ROOT))$' \
  '^/plugins/[^/]*@(rigger|superpowers-marketplace)(/[0-9]+)?(/(scope|version|projectPath))?$' \
  '^/rigger(/source(/(repo|path))?)?$')
j1=
j2=
j3=
s1=
s2=
s3=
crlf=
head=${out%%"$nl"*}
tab=${head#?}
cr=${head%?}
case $out in
  "$cr$tab$nl"*"$nl"x"$cr") ;;
  "$cr$tab$nl"*"$nl"x) crlf=1 ;;
  *) out= ;;
esac
if [ -n "$out" ]; then
  out=${out#*"$nl"}
  eval "${out%"$nl"*}"
else
  tab=$(printf '\t')
  cr=$(printf '\r')
fi

# -- os, claude-version -------------------------------------------------------------------------
# No child reads doctor.sh's stdin.
# shellcheck disable=SC2217
kernel=$(uname -s </dev/null 2>/dev/null)
first_line "$kernel"
kernel=$line
case $kernel in
  Darwin) os=macOS ;;
  Linux) os=Linux ;;
  MINGW* | MSYS*) os=Windows ;;
  *) os= ;;
esac
if [ -n "$os" ]; then
  check os OK "$os" 'uname -s'
else
  check os WARN "unknown system ${kernel:-(empty)}" 'uname -s'
fi

cv=
case ${AI_AGENT-} in
  claude-code_*_*)
    cv=${AI_AGENT#claude-code_}
    cv=${cv%%_*}
    ;;
esac
major=${cv%%-*}
rest=${cv#*-}
minor=${rest%%-*}
patch=${rest#*-}
case $cv in *-*-*) ;; *) cv= ;; esac
case "$major/$minor/$patch" in /* | */ | *//* | *[!0-9/]*) cv= ;; esac
if [ -z "$cv" ]; then
  check claude-version WARN unknown 'AI_AGENT'
elif [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && { [ "$minor" -lt 1 ] || { [ "$minor" -eq 1 ] && [ "$patch" -lt 317 ]; }; }; }; then
  check claude-version WARN "$major.$minor.$patch, below 2.1.317" 'AI_AGENT'
  if [ "${CLAUDE_CODE_ENTRYPOINT-}" = claude-desktop ]; then
    fix claude-version 'update the Claude desktop app'
  else
    fix claude-version 'claude update'
  fi
else
  check claude-version OK "$major.$minor.$patch" 'AI_AGENT'
fi

# -- hook-shell, git (on Windows doctor.ps1 reports them) ---------------------------------------
distro=
if [ "$os" != Windows ] && [ "$os" != macOS ] && [ -f "$fs/etc/os-release" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    value=${line#*=}
    value=${value#\"}
    value=${value%\"}
    case $line in
      ID=*) distro="$value $distro" ;;
      ID_LIKE=*) distro="$distro $value" ;;
    esac
  done <"$fs/etc/os-release"
fi
clt=
if [ "$os" = macOS ] && command -v xcode-select >/dev/null 2>&1 && xcode-select -p </dev/null >/dev/null 2>&1; then
  clt=1
fi

if [ "$os" != Windows ]; then
  found=$(command -v sh 2>/dev/null) || found=
  if [ -n "$found" ]; then
    check hook-shell OK "$found" 'command -v sh'
  else
    check hook-shell MISSING 'sh not on PATH' 'command -v sh'
  fi

  found=$(command -v git 2>/dev/null) || found=
  if [ -n "$found" ] && { [ "$os" != macOS ] || [ -n "$clt" ] || [ "$found" != "$fs/usr/bin/git" ]; }; then
    version=$(git --version </dev/null 2>/dev/null)
    first_line "$version"
    version=${line#git version }
    check git OK "${version:-found at $found}" 'git --version'
  else
    if [ -n "$found" ]; then
      check git MISSING '/usr/bin/git without the Command Line Tools' 'xcode-select -p'
    else
      check git MISSING 'not on PATH' 'command -v git'
    fi
    if [ "$os" = macOS ]; then
      fix git 'xcode-select --install'
    else
      linux_install git
      fix git "$cmd"
    fi
  fi
fi

# -- python, python-bare ------------------------------------------------------------------------
found=$(
  unset CLAUDE_PLUGIN_DATA
  exec sh "$root/bin/py.sh" -c 'import sys; print(sys.executable, sys.version.split()[0])' </dev/null 2>/dev/null
)
first_line "$found"
found=$line
case $found in
  *' '[0-9]*) check python OK "$found" 'bin/py.sh, the search the hooks use' ;;
  *)
    check python MISSING 'no Python 3.9+ found' 'bin/py.sh, the search the hooks use'
    if [ "$os" = Windows ] && command -v winget >/dev/null 2>&1; then
      cmd='winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements'
    elif [ "$os" = Windows ]; then
      cmd='install Python 3.9+ from python.org'
    elif [ "$os" = macOS ] && [ -z "$clt" ]; then
      cmd='xcode-select --install'
    elif [ "$os" = macOS ] && { command -v brew >/dev/null 2>&1 || [ -x "$fs/opt/homebrew/bin/brew" ] || [ -x "$fs/usr/local/bin/brew" ]; }; then
      cmd='brew install python'
    elif [ "$os" = macOS ]; then
      cmd='install Python 3.9+ from python.org'
    else
      # A python3 that answers is older than 3.9 (py.sh rejected it), so the package manager's may be too.
      old=$(python3 --version </dev/null 2>&1)
      first_line "$old"
      old=$line
      case $old in
        'Python '[0-9]*) cmd="install Python 3.9+ (found ${old#Python })" ;;
        *) linux_install python3 ;;
      esac
    fi
    fix python "$cmd"
    ;;
esac

# On macOS without the Command Line Tools /usr/bin/python3 opens their installer, so nothing runs there.
if [ "$os" != macOS ] || [ -n "$clt" ]; then
  bare=$(python --version </dev/null 2>&1)
  first_line "$bare"
  bare=$line
  bare3=$(python3 --version </dev/null 2>&1)
  first_line "$bare3"
  bare3=$line
  case "$bare/$bare3" in
    'Python '[0-9]*'/Python '[0-9]*)
      bare=${bare#Python }
      bare=${bare%% *}
      bare3=${bare3#Python }
      bare3=${bare3%% *}
      if [ "$bare" != "$bare3" ]; then
        check python-bare INFO "python is $bare, python3 is $bare3" 'python --version, python3 --version'
      fi
      ;;
  esac
fi

# -- marketplace, plugins, settings ---------------------------------------------------------------
# *_bad holds the file's path when it is not valid JSON: Claude Code rejects such a file, so a row that
# reads it is unknown and gets no FIX.
settings_bad=
installed_bad=
markets_bad=
[ -z "$f1" ] || [ "$s1" = 0 ] || settings_bad=$how_settings
[ -z "$f2" ] || [ "$s2" = 0 ] || installed_bad=$how_installed
[ -z "$f3" ] || [ "$s3" = 0 ] || markets_bad=$how_markets
trim "$j1"
settings=$trimmed
trim "$j2"
installed=$trimmed
trim "$j3"
markets=$trimmed
plugins_bad=${installed_bad:-$settings_bad}
[ -z "$settings_bad" ] || check settings-file WARN "$settings_bad is not valid JSON" 'parsed by doctor.sh'
[ -z "$installed_bad" ] || check installed-plugins-file WARN "$installed_bad is not valid JSON" 'parsed by doctor.sh'
[ -z "$markets_bad" ] || check marketplaces-file WARN "$markets_bad is not valid JSON" 'parsed by doctor.sh'

if [ -n "$markets_bad" ]; then
  check marketplace WARN "unknown: $markets_bad is not valid JSON" "$how_markets"
elif get "$markets" /rigger && [ "$jtype" = o ]; then
  detail=added
  if get "$markets" /rigger/source/repo && [ "$jtype" = s ] && [ -n "$jval" ]; then
    detail=$jval
  elif get "$markets" /rigger/source/path && [ "$jtype" = s ] && [ -n "$jval" ]; then
    detail=$jval
  fi
  check marketplace OK "$detail" "$how_markets"
else
  check marketplace MISSING 'rigger not added' "$how_markets"
  fix marketplace 'claude plugin marketplace add Borcioo/rigger-claude-code'
fi

required='rigger-core rigger-rules rigger-lessons'
[ "$os" != Windows ] || required="$required rigger-windows"
names=$required
rest=$installed$nl
while [ -n "$rest" ]; do
  line=${rest%%"$nl"*}
  rest=${rest#*"$nl"}
  case ${line%%"$tab"*} in
    /plugins/rigger-*@rigger)
      name=${line#/plugins/}
      name=${name%%@*}
      # The name reaches FIX lines a person pastes into a shell.
      case $name in *[!a-z0-9-]*) continue ;; esac
      case " $names " in *" $name "*) ;; *) names="$names $name" ;; esac
      ;;
  esac
done
for name in $names; do
  plugin_row "plugin $name" "$name@rigger" MISSING ||
    fix "plugin $name" "claude plugin install $name@rigger"
done
if ! plugin_row superpowers superpowers@superpowers-marketplace WARN; then
  fix superpowers 'claude plugin marketplace add obra/superpowers-marketplace'
  fix superpowers 'claude plugin install superpowers@superpowers-marketplace'
fi

compact=
if [ -n "$settings_bad" ]; then
  compact=unknown
elif get "$settings" /autoCompactWindow; then
  case $jtype:$jval in
    n: | n:*[!0-9]*) compact='not a number' ;;
    n:*) compact=$jval ;;
    *) compact='not a number' ;;
  esac
fi
case $compact in
  unknown) check autoCompactWindow WARN "unknown: $settings_bad is not valid JSON" "$how_settings" ;;
  '') check autoCompactWindow WARN 'not set; harness-setup offers it' "$how_settings" ;;
  'not a number') check autoCompactWindow WARN "$compact" "$how_settings" ;;
  *)
    if [ "$compact" -lt 560000 ]; then
      check autoCompactWindow OK "$compact" "$how_settings"
    else
      check autoCompactWindow WARN "$compact, at or above compact_guard's cap 560000" "$how_settings"
    fi
    ;;
esac
case $compact in
  [0-9]*) [ "$compact" -ge 560000 ] && fix autoCompactWindow /rigger-core:harness-setup ;;
  unknown) ;;
  *) fix autoCompactWindow /rigger-core:harness-setup ;;
esac

if [ "$os" = Windows ]; then
  if [ -n "$settings_bad" ]; then
    check powershell-tool WARN "unknown: $settings_bad is not valid JSON" "$how_settings env"
  elif get "$settings" /env/CLAUDE_CODE_USE_POWERSHELL_TOOL && [ "$jtype" = s ] && [ "$jval" = 1 ]; then
    check powershell-tool OK 'CLAUDE_CODE_USE_POWERSHELL_TOOL=1' "$how_settings env"
  else
    check powershell-tool WARN 'CLAUDE_CODE_USE_POWERSHELL_TOOL is not 1' "$how_settings env"
  fi
fi

if [ -n "$settings_bad" ]; then
  check worktree-root WARN "unknown: $settings_bad is not valid JSON" "$how_settings env"
elif get "$settings" /env/HARNESS_WORKTREE_ROOT && [ "$jtype" = s ] && [ -n "$jval" ]; then
  check worktree-root OK "$jval" "$how_settings env"
else
  check worktree-root WARN 'HARNESS_WORKTREE_ROOT not set' "$how_settings env"
  fix worktree-root /rigger-core:harness-setup
fi
exit 0
