# Shell checklist

- `set -e` does not stop the script inside an `if` or `while` condition, a `&&` or `||` list, or a
  function called from one; and `local x=$(cmd)` takes `local`'s exit status, not `cmd`'s.
- A pipeline's status is its last command's unless `pipefail` is set: a failing first stage goes
  unseen.
- With `pipefail`, `grep -q` or `head` closing the pipe early can fail the writer with SIGPIPE
  (status 141).
- A variable set inside `cmd | while read …` is lost after the loop: the loop runs in a subshell.
- `exit` inside `( … )` or `$( … )` leaves only the subshell.
- `$( … )` strips every trailing newline of the output.
- `[ "$a" \< "$b" ]` and `[ "$a" = "$b" ]` compare strings; numbers use `-lt` and `-eq`.
- An argument that may start with `-` is passed after `--` (`rm -- "$file"`).
- Temporary files come from `mktemp` and are removed by a `trap … EXIT`; a fixed name under
  `/tmp` collides and can be planted.
- `|| true` and `2>/dev/null` on a step whose failure matters turn an error into success.
- `echo` treats `-n` and backslashes differently between shells: output of outside text goes
  through `printf '%s\n'`.
- `sed -i` takes a suffix argument on BSD and macOS and none on GNU.
- A heredoc inside `$( … )` is misparsed by bash 3.2, the `/bin/sh` of macOS.
- `#!/bin/sh` scripts run under dash, busybox or bash 3.2: no `[[ ]]`, arrays, `local -n` or
  `$'…'`.
