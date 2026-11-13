# doctor.ps1: on native Windows, find Git Bash the way Claude Code does, report git, and Python when there is
# no Git Bash (doctor.sh checks the rest under that bash). Prints tab-separated CHECK/FIX/BASH lines and writes
# nothing. Exit 0 on any finding; exit 2 with one reason line on its own error. Windows PowerShell 5.1 syntax.
# -ProgramFiles/-ProgramFilesX86 are for tests: the defaults are the drive-C folders Claude Code hard-codes,
# not $env:ProgramFiles. -PrintDefaults (tests) prints the two folders, one per line, and exits 0.
param(
    [string]$ProgramFiles = ([string][char]67 + ':\Program Files'),
    [string]$ProgramFilesX86 = ([string][char]67 + ':\Program Files (x86)'),
    [switch]$PrintDefaults
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
trap { Write-Output ('doctor.ps1: ' + $_.Exception.Message); exit 2 }

if ($PrintDefaults) { Write-Output $ProgramFiles; Write-Output $ProgramFilesX86; exit 0 }

function Emit([string[]]$Fields) { Write-Output ($Fields -join "`t") }

# 5.1's Test-Path throws on characters such as < or |; such a path cannot exist.
function Test-File([string]$Path) {
    try { return [bool](Test-Path -LiteralPath $Path -PathType Leaf) } catch { return $false }
}

function Test-Dir([string]$Path) {
    try { return [bool](Test-Path -LiteralPath $Path -PathType Container) } catch { return $false }
}

function Find-App([string]$Name) {
    Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
}

if (-not $ProgramFiles -or -not $ProgramFilesX86) { throw '-ProgramFiles and -ProgramFilesX86 must not be empty' }

$installGit = 'https://git-scm.com/downloads/win'
$installPython = 'install Python 3.9+ from python.org'
if (Find-App 'winget') {
    $installGit = 'winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements'
    $installPython = 'winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements'
}
$how = 'CLAUDE_CODE_GIT_BASH_PATH, Program Files\Git\bin, Program Files (x86)\Git\bin, git on PATH (Claude Code''s order)'
$bash = $null
$source = $null

$fromEnv = $env:CLAUDE_CODE_GIT_BASH_PATH
if ($fromEnv) {
    # Split by hand: 5.1's [IO.Path]::GetFileName throws on characters such as a quote. -notcontains
    # ignores case, as Claude Code lowercases the name.
    $name = ($fromEnv -split '[\\/]')[-1]
    $reason = $null
    if (@('bash.exe', 'sh.exe', 'bash', 'sh') -notcontains $name) { $reason = 'is not a bash/sh binary' }
    elseif (-not (Test-File $fromEnv)) { $reason = 'not found' }
    else { $bash = $fromEnv; $source = 'CLAUDE_CODE_GIT_BASH_PATH' }
    if ($reason) {
        Emit 'CHECK', 'hook-shell', 'WARN', "CLAUDE_CODE_GIT_BASH_PATH=$fromEnv $reason; Claude Code ignores it", 'its file name and whether the file exists'
    }
}
if (-not $bash) {
    foreach ($root in @($ProgramFiles, $ProgramFilesX86)) {
        $candidate = [IO.Path]::Combine($root, 'Git\bin\bash.exe')
        if (Test-File $candidate) { $bash = $candidate; $source = 'default install'; break }
    }
}
$git = Find-App 'git'
if (-not $bash -and $git) {
    $candidate = [IO.Path]::Combine([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($git.Path)), 'bin\bash.exe')
    if (Test-File $candidate) { $bash = $candidate; $source = 'next to git on PATH' }
}

if ($bash) {
    Emit 'CHECK', 'hook-shell', 'OK', ($source + ': ' + $bash), $how
    Emit 'BASH', $bash
} else {
    Emit 'CHECK', 'hook-shell', 'MISSING', 'no Git Bash: Claude Code runs every hook in PowerShell, where the Rigger hooks fail', $how
    Emit 'FIX', 'hook-shell', $installGit
    Emit 'FIX', 'hook-shell', 'set "CLAUDE_CODE_GIT_BASH_PATH": "<path to bin\bash.exe>" under env in settings.json'
}

if ($git) {
    Emit 'CHECK', 'git', 'OK', $git.Path, 'Get-Command git'
} else {
    Emit 'CHECK', 'git', 'MISSING', 'git is not on PATH', 'Get-Command git'
    # Without Git Bash the hook-shell FIX above already installs Git. With it, Git is installed and only its
    # cmd folder is missing from PATH; it is named only for <Git>\bin\bash.exe with a <Git>\cmd folder.
    if ($bash) {
        $binFolder = [IO.Path]::GetDirectoryName($bash)
        $gitFolder = if ($binFolder) { [IO.Path]::GetDirectoryName($binFolder) }
        $cmdFolder = if ($gitFolder) { [IO.Path]::Combine($gitFolder, 'cmd') }
        $inBin = $binFolder -and ([IO.Path]::GetFileName($binFolder) -eq 'bin')
        if ($inBin -and $cmdFolder -and (Test-Dir $cmdFolder)) { Emit 'FIX', 'git', ('add ' + $cmdFolder + ' to PATH') }
        else { Emit 'FIX', 'git', 'add the cmd folder of Git for Windows to PATH' }
    }
}

if ($bash) { exit 0 }

# Returns @($true, '<version> <path>') for Python 3.9+, else @($false, '<why>').
function Get-PythonProbe([string]$Label, [string]$Name, [string[]]$Arguments) {
    if (-not (Find-App $Name)) { return @($false, "${Label}: not found") }
    # A native call that writes to stderr throws under Stop in 5.1.
    $ErrorActionPreference = 'Continue'
    $out = @(& $Name @Arguments 2>$null)
    $exit = $LASTEXITCODE
    if ($exit -eq 9009) { return @($false, "${Label}: Microsoft Store alias, no Python installed (exit 9009)") }
    if ($exit -ne 0) { return @($false, "${Label}: exit $exit") }
    $line = @($out | Where-Object { "$_" -match '^(\d+)\.(\d+)\S*\s+\S' }) | Select-Object -First 1
    if (-not $line) { return @($false, "${Label}: no version in its output") }
    $null = "$line" -match '^(\d+)\.(\d+)'
    if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 9)) {
        return @($false, "${Label}: $(("$line" -split '\s+')[0]) is older than 3.9")
    }
    return @($true, "$line")
}

$code = 'import sys;print(sys.version.split()[0],sys.executable)'
$first = Get-PythonProbe 'py -3' 'py' @('-3', '-c', $code)
$probe = $first
if (-not $first[0]) { $probe = Get-PythonProbe 'python' 'python' @('-c', $code) }
$how = 'py -3 -c, then python -c, needing 3.9+'
if ($probe[0]) {
    Emit 'CHECK', 'python', 'OK', $probe[1], $how
} else {
    Emit 'CHECK', 'python', 'MISSING', ('no Python 3.9+ (' + $first[1] + '; ' + $probe[1] + ')'), $how
    Emit 'FIX', 'python', $installPython
}
exit 0
