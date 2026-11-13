<#
.SYNOPSIS
  Red run in place: break one file on purpose (a "red run" proves the tests fail without the code), run the test command, always put the file back.

.DESCRIPTION
  The global rule is that a red run never swaps working files by hand: a tool timeout kills the
  shell before `finally` runs, and `git checkout` as the restore discards every other uncommitted
  edit in the file. This script makes the in-place red run safe:

  - the edit is a literal replace that must match exactly -Count times (proof the mutation applied);
  - the original bytes and the hash of the mutated file go into `<file>.safe-edit-backup` first;
  - the command runs, then the file is restored from that backup and its hash is checked;
  - a backup left by a killed run is restored by the next call (or by -Restore) when the file is
    still exactly as that run left it; if it changed since, the script refuses and says so.

  Line endings and a UTF-8 BOM are kept: in a CRLF file, "`n" in -Find/-Replace becomes "`r`n".

  -Run requires -ExpectRan, -ExpectRan requires -Run, an empty -Run is refused. Exit codes with -Run: 0 = red as expected (the command failed and -ExpectRan matched),
  1 = the script itself failed, 2 = refused (usage, no or too many matches, a stale backup that
  cannot be restored safely), 3 = the output does not match -ExpectRan (no test may have run),
  4 = not red (the command passed: the mutation was not caught). Without -Run the edit is applied
  and kept (exit 0) or refused (exit 2); there is no backup, so -Restore cannot undo it: revert with
  the inverse -Find/-Replace.

.EXAMPLE
  pwsh -NoProfile -File <rigger-windows root>/tools/safe-edit/safe-edit.ps1 -Path src\Guard.php `
    -Find 'if ( ! $allowed ) {' -Replace 'if ( false ) {' `
    -Run 'vendor/bin/phpunit --filter Guard_Test' -ExpectRan 'Tests: [1-9]'

.EXAMPLE
  pwsh -NoProfile -File <rigger-windows root>/tools/safe-edit/safe-edit.ps1 -Path src\Guard.php -Restore
#>
[CmdletBinding(DefaultParameterSetName = 'Edit')]
param(
  [Parameter(Mandatory)][string]$Path,
  [Parameter(Mandatory, ParameterSetName = 'Edit')][string]$Find,
  [Parameter(Mandatory, ParameterSetName = 'Edit')][AllowEmptyString()][string]$Replace,
  [Parameter(ParameterSetName = 'Edit')][ValidateRange(1, 10000)][int]$Count = 1,
  [Parameter(ParameterSetName = 'Edit')][string]$Run,
  [Parameter(ParameterSetName = 'Edit')][string]$ExpectRan,
  [Parameter(ParameterSetName = 'Edit')][string]$WorkingDirectory,
  [Parameter(Mandatory, ParameterSetName = 'Restore')][switch]$Restore
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$strictUtf8 = [Text.UTF8Encoding]::new($false, $true)

function Stop-Refused([string]$message) {
  Write-Host "safe-edit: REFUSED — $message"
  exit 2
}

function Get-Sha256([byte[]]$bytes) {
  [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))
}

function Restore-FromBackup([string]$file, [string]$backup) {
  $state = [IO.File]::ReadAllText($backup, $strictUtf8) | ConvertFrom-Json
  $original = [Convert]::FromBase64String($state.original)
  [IO.File]::WriteAllBytes($file, $original)
  if ((Get-Sha256 ([IO.File]::ReadAllBytes($file))) -ne $state.originalSha256) {
    throw "restored $file but its hash does not match the backup; the backup is kept at $backup"
  }
  [IO.File]::Delete($backup)
}

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Stop-Refused "no such file: $Path" }
$file = (Resolve-Path -LiteralPath $Path).ProviderPath
$backup = "$file.safe-edit-backup"

# A backup left by a killed run: restore it only while the file is exactly as that run left it.
if (Test-Path -LiteralPath $backup) {
  $state = [IO.File]::ReadAllText($backup, $strictUtf8) | ConvertFrom-Json
  $current = Get-Sha256 ([IO.File]::ReadAllBytes($file))
  if ($current -eq $state.originalSha256) {
    [IO.File]::Delete($backup)
    Write-Host "safe-edit: removed a stale backup; $file already holds its original content."
  }
  elseif ($current -eq $state.mutatedSha256) {
    Restore-FromBackup $file $backup
    Write-Host "safe-edit: restored $file from a backup left by an earlier, interrupted run."
  }
  else {
    Stop-Refused ("$backup exists, but $file changed after that run. Compare the two, put the " +
      "right content back by hand, then delete the backup.")
  }
}

if ($Restore) {
  Write-Host "safe-edit: nothing else to restore for $file."
  exit 0
}

if ($Find -ceq $Replace) { Stop-Refused '-Find and -Replace are the same; that is no mutation.' }
if ($Find -eq '') { Stop-Refused '-Find is empty.' }
# Without a command the edit would be kept, exit 0: a red run that never ran.
if ($PSBoundParameters.ContainsKey('Run') -and -not $Run) { Stop-Refused '-Run is empty.' }
if ($PSBoundParameters.ContainsKey('ExpectRan') -and -not $Run) { Stop-Refused '-ExpectRan needs -Run.' }
if ($Run -and -not $ExpectRan) {
  Stop-Refused ("-Run needs -ExpectRan '<regex>' matching a line only a test run prints (e.g. '# tests [1-9]'); " +
    'without it a command that ran no test counts as red.')
}

$bytes = [IO.File]::ReadAllBytes($file)
$hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
$bodyOffset = if ($hasBom) { 3 } else { 0 }
try { $text = $strictUtf8.GetString($bytes, $bodyOffset, $bytes.Length - $bodyOffset) }
catch { Stop-Refused "$file is not valid UTF-8; edit it with the Edit tool instead." }

# Match the file's line endings when it uses one style throughout.
$crlf = ([regex]::Matches($text, "`r`n")).Count
$lf = ([regex]::Matches($text, "(?<!`r)`n")).Count
if ($crlf -gt 0 -and $lf -eq 0) {
  $Find = ($Find -replace "`r`n", "`n") -replace "`n", "`r`n"
  $Replace = ($Replace -replace "`r`n", "`n") -replace "`n", "`r`n"
}
elseif ($lf -gt 0 -and $crlf -eq 0) {
  $Find = $Find -replace "`r`n", "`n"
  $Replace = $Replace -replace "`r`n", "`n"
}

$found = 0
$at = 0
while (($at = $text.IndexOf($Find, $at, [StringComparison]::Ordinal)) -ge 0) {
  $found++
  $at += $Find.Length
}
if ($found -ne $Count) {
  Stop-Refused "expected $Count match(es) of -Find in $file, found $found; nothing changed."
}

$newText = $text.Replace($Find, $Replace)
$newBody = [Text.UTF8Encoding]::new($false).GetBytes($newText)
$newBytes = if ($hasBom) { [byte[]](@(0xEF, 0xBB, 0xBF) + $newBody) } else { $newBody }

if (-not $Run) {
  [IO.File]::WriteAllBytes($file, $newBytes)
  Write-Host "safe-edit: replaced $found match(es) in $file (line endings and BOM kept)."
  exit 0
}

$state = [ordered]@{
  file           = $file
  original       = [Convert]::ToBase64String($bytes)
  originalSha256 = Get-Sha256 $bytes
  mutatedSha256  = Get-Sha256 $newBytes
  started        = (Get-Date -Format s)
  pid            = $PID
}
[IO.File]::WriteAllText($backup, ($state | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

$exitCode = $null
$captured = [Collections.Generic.List[string]]::new()
try {
  [IO.File]::WriteAllBytes($file, $newBytes)
  if ((Get-Sha256 ([IO.File]::ReadAllBytes($file))) -ne $state.mutatedSha256) {
    throw "the mutated content did not land in $file"
  }
  Write-Host "safe-edit: mutation applied ($found match(es)) to $file; running: $Run"
  if ($WorkingDirectory) { Push-Location -LiteralPath $WorkingDirectory }
  try {
    & pwsh -NoProfile -Command $Run 2>&1 | ForEach-Object {
      $line = "$_"
      $captured.Add($line)
      Write-Host $line
    }
    $exitCode = $LASTEXITCODE
  }
  finally {
    if ($WorkingDirectory) { Pop-Location }
  }
}
finally {
  Restore-FromBackup $file $backup
  Write-Host "safe-edit: restored $file (hash checked)."
}

if ($ExpectRan -and -not ($captured | Where-Object { $_ -match $ExpectRan })) {
  Write-Host "safe-edit: NO EVIDENCE — the output never matched -ExpectRan '$ExpectRan'; maybe no test ran (exit code $exitCode)."
  exit 3
}
if ($exitCode -eq 0) {
  Write-Host 'safe-edit: NOT RED — the command passed with the mutation in place; the check did not catch it.'
  exit 4
}
Write-Host "safe-edit: RED as expected — the command exited $exitCode with the mutation in place."
exit 0
