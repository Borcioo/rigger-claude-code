<#
.SYNOPSIS
  Self-test for safe-edit.ps1. Exit 0 when every case passes.

.EXAMPLE
  pwsh -NoProfile -File <rigger-windows root>\tools\safe-edit\tests\safe-edit.tests.ps1
  # Against a mutated copy (red run of the tool itself):
  pwsh -NoProfile -File ...\safe-edit.tests.ps1 -Script $env:TEMP\safe-edit-red\safe-edit.ps1
#>
param(
  [string]$Script = (Join-Path (Split-Path -Parent $PSScriptRoot) 'safe-edit.ps1')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = [IO.Path]::GetTempPath()
$work = Join-Path $root ('safe-edit-test-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + "-$PID")
New-Item -ItemType Directory -Path $work | Out-Null

$utf8 = [Text.UTF8Encoding]::new($false)
$failures = [Collections.Generic.List[string]]::new()
$passed = 0

function New-Fixture([string]$name, [string]$text, [switch]$Bom) {
  $p = Join-Path $work $name
  $body = $utf8.GetBytes($text)
  $bytes = if ($Bom) { [byte[]](@(0xEF, 0xBB, 0xBF) + $body) } else { $body }
  [IO.File]::WriteAllBytes($p, $bytes)
  $p
}

function Invoke-SafeEdit([string[]]$arguments) {
  $output = & pwsh -NoProfile -File $Script @arguments 2>&1 | ForEach-Object { "$_" }
  [pscustomobject]@{ Code = $LASTEXITCODE; Output = ($output -join "`n") }
}

function Test-Case([string]$name, [scriptblock]$body) {
  try {
    & $body
    $script:passed++
    Write-Host "PASS  $name"
  }
  catch {
    $script:failures.Add("$name — $($_.Exception.Message)")
    Write-Host "FAIL  $name — $($_.Exception.Message)"
  }
}

function Assert-Equal($expected, $actual, [string]$what) {
  if ($expected -ne $actual) { throw "$what`: expected <$expected>, got <$actual>" }
}

function Read-Bytes([string]$p) { [IO.File]::ReadAllBytes($p) }
function Read-Text([string]$p) { [IO.File]::ReadAllText($p, $utf8) }

try {
  Test-Case 'edit mode replaces one match and keeps LF' {
    $f = New-Fixture 'lf.txt' "a = 1;`nif (ok) {`n  go();`n}`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'if (ok) {', '-Replace', 'if (true) {')
    Assert-Equal 0 $r.Code 'exit code'
    Assert-Equal "a = 1;`nif (true) {`n  go();`n}`n" (Read-Text $f) 'content'
  }

  Test-Case 'edit mode keeps CRLF and a BOM, and maps a LF in -Find to CRLF' {
    $f = New-Fixture 'crlf.txt' "naïve`r`nif (ok) {`r`n  go();`r`n" -Bom
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', "if (ok) {`n  go();", '-Replace', "if (ok) {`n  stop();")
    Assert-Equal 0 $r.Code 'exit code'
    $bytes = Read-Bytes $f
    Assert-Equal 0xEF $bytes[0] 'BOM byte 1'
    Assert-Equal "naïve`r`nif (ok) {`r`n  stop();`r`n" ($utf8.GetString($bytes, 3, $bytes.Length - 3)) 'content'
  }

  Test-Case 'no match is refused and the file is untouched' {
    $f = New-Fixture 'nomatch.txt' "alpha`n"
    $before = Get-FileHash -LiteralPath $f
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'beta', '-Replace', 'gamma')
    Assert-Equal 2 $r.Code 'exit code'
    Assert-Equal $before.Hash (Get-FileHash -LiteralPath $f).Hash 'hash'
  }

  Test-Case 'two matches with -Count 1 are refused' {
    $f = New-Fixture 'twice.txt' "x x`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'x', '-Replace', 'y')
    Assert-Equal 2 $r.Code 'exit code'
    Assert-Equal "x x`n" (Read-Text $f) 'content'
  }

  Test-Case 'red run: failing command → exit 0, file restored, backup gone, mutation seen by the command' {
    $f = New-Fixture 'red.txt' "guard = on`n"
    $cmd = "if ((Get-Content -Raw -LiteralPath '$f') -match 'guard = off') { '# tests 1'; exit 1 } else { exit 0 }"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'guard = on', '-Replace', 'guard = off', '-Run', $cmd, '-ExpectRan', '# tests [1-9]')
    Assert-Equal 0 $r.Code 'exit code'
    Assert-Equal "guard = on`n" (Read-Text $f) 'restored content'
    Assert-Equal $false (Test-Path -LiteralPath "$f.safe-edit-backup") 'backup removed'
  }

  Test-Case 'not red: passing command → exit 4, file restored' {
    $f = New-Fixture 'green.txt' "guard = on`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'guard = on', '-Replace', 'guard = off', '-Run', "'# tests 1'; exit 0", '-ExpectRan', '# tests [1-9]')
    Assert-Equal 4 $r.Code 'exit code'
    Assert-Equal "guard = on`n" (Read-Text $f) 'restored content'
  }

  Test-Case '-Run without -ExpectRan is refused before the file is touched' {
    $f = New-Fixture 'noexpect.txt' "guard = on`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'guard = on', '-Replace', 'guard = off', '-Run', "'# tests 1'; exit 1")
    Assert-Equal 2 $r.Code 'exit code'
    Assert-Equal "guard = on`n" (Read-Text $f) 'content'
    Assert-Equal $false (Test-Path -LiteralPath "$f.safe-edit-backup") 'no backup written'
  }

  Test-Case 'a replacement differing only in case is a mutation' {
    $f = New-Fixture 'case.txt' "flag = True`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'True', '-Replace', 'true')
    Assert-Equal 0 $r.Code 'exit code'
    Assert-Equal "flag = true`n" (Read-Text $f) 'content'
  }

  Test-Case 'an identical -Find and -Replace are refused' {
    $f = New-Fixture 'same.txt' "flag = True`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'True', '-Replace', 'True')
    Assert-Equal 2 $r.Code 'exit code'
  }

  Test-Case 'an empty -Run is refused, the file untouched' {
    $f = New-Fixture 'emptyrun.txt' "guard = on`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'guard = on', '-Replace', 'guard = off', '-Run', '')
    Assert-Equal 2 $r.Code 'exit code'
    Assert-Equal "guard = on`n" (Read-Text $f) 'content'
  }

  Test-Case 'an empty -Run with -ExpectRan is refused, the file untouched' {
    $f = New-Fixture 'emptyrun2.txt' "guard = on`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'guard = on', '-Replace', 'guard = off', '-Run', '', '-ExpectRan', 'x')
    Assert-Equal 2 $r.Code 'exit code'
    Assert-Equal "guard = on`n" (Read-Text $f) 'content'
  }

  Test-Case '-ExpectRan without -Run is refused, the file untouched' {
    $f = New-Fixture 'expectonly.txt' "guard = on`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'guard = on', '-Replace', 'guard = off', '-ExpectRan', 'x')
    Assert-Equal 2 $r.Code 'exit code'
    Assert-Equal "guard = on`n" (Read-Text $f) 'content'
  }

  Test-Case 'no evidence: -ExpectRan never matched → exit 3, file restored' {
    $f = New-Fixture 'noevidence.txt' "guard = on`n"
    $r = Invoke-SafeEdit @('-Path', $f, '-Find', 'guard = on', '-Replace', 'guard = off', '-Run', "'# tests 0'; exit 1", '-ExpectRan', '# tests [1-9]')
    Assert-Equal 3 $r.Code 'exit code'
    Assert-Equal "guard = on`n" (Read-Text $f) 'restored content'
  }

  Test-Case 'killed run leaves a backup; the next call restores the file' {
    $f = New-Fixture 'killed.txt' "guard = on`nother edit kept`n"
    # The command kills the safe-edit process itself, as a tool timeout would.
    $kill = 'Stop-Process -Id (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId -Force'
    $null = Invoke-SafeEdit @('-Path', $f, '-Find', 'guard = on', '-Replace', 'guard = off', '-Run', $kill, '-ExpectRan', 'x')
    Assert-Equal "guard = off`nother edit kept`n" (Read-Text $f) 'content after the kill (mutation left in place)'
    Assert-Equal $true (Test-Path -LiteralPath "$f.safe-edit-backup") 'backup left behind'
    $r = Invoke-SafeEdit @('-Path', $f, '-Restore')
    Assert-Equal 0 $r.Code 'restore exit code'
    Assert-Equal "guard = on`nother edit kept`n" (Read-Text $f) 'restored content'
    Assert-Equal $false (Test-Path -LiteralPath "$f.safe-edit-backup") 'backup removed'
  }

  Test-Case 'a backup whose file changed since is refused, nothing overwritten' {
    $f = New-Fixture 'drifted.txt' "guard = on`n"
    $kill = 'Stop-Process -Id (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId -Force'
    $null = Invoke-SafeEdit @('-Path', $f, '-Find', 'guard = on', '-Replace', 'guard = off', '-Run', $kill, '-ExpectRan', 'x')
    [IO.File]::WriteAllText($f, "someone edited this`n", $utf8)
    $r = Invoke-SafeEdit @('-Path', $f, '-Restore')
    Assert-Equal 2 $r.Code 'exit code'
    Assert-Equal "someone edited this`n" (Read-Text $f) 'content untouched'
    Assert-Equal $true (Test-Path -LiteralPath "$f.safe-edit-backup") 'backup kept'
  }
}
finally {
  Remove-Item -LiteralPath $work -Recurse -Force
}

Write-Host ''
Write-Host "safe-edit tests: $passed passed, $($failures.Count) failed."
if ($failures.Count -gt 0) { $failures | ForEach-Object { Write-Host "  $_" }; exit 1 }
exit 0
