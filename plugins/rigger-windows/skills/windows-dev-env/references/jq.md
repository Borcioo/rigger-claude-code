# jq on Windows

**jq** (winget `jqlang.jq`), where installed, is on PATH in both shells; if a manual copy
`~/bin/jq.exe` shadows it in Git Bash, copy the new `jq.exe` over it after `winget upgrade jqlang.jq`. Use it for one-shot
extraction from a file or a command's JSON output, for big files, and for
JSON whose keys differ only in case (`~/.claude.json`), which `ConvertFrom-Json` refuses. In
PowerShell 7 a single-quoted filter with double quotes inside works as written:
`jq -r '.projects | keys[] | select(test("<project>"; "i"))' $HOME\.claude.json`. Each call spawns a
process, so inside a loop parse once with `ConvertFrom-Json -AsHashtable`. `gh` has jq built in:
`gh api … --jq '.field'`.
