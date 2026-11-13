<#
.SYNOPSIS
  Self-test for mem-check.ps1. Exit 0 when every case passes.

.EXAMPLE
  pwsh -NoProfile -File <rigger-windows root>\tools\mem-check\mem-check.tests.ps1
  # Against a mutated copy (red run of the tool itself):
  pwsh -NoProfile -File ...\mem-check.tests.ps1 -Script $env:TEMP\mem-check-red\mem-check.ps1
#>
param(
  [string]$Script = (Join-Path $PSScriptRoot 'mem-check.ps1')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$failures = [Collections.Generic.List[string]]::new()
$passed = 0
$gib = [long]1GB
$inv = [Globalization.CultureInfo]::InvariantCulture

function Invoke-MemCheck([string[]]$arguments) {
  $output = & pwsh -NoProfile -File $Script @arguments 2>&1 | ForEach-Object { "$_" }
  [pscustomobject]@{ Code = $LASTEXITCODE; Output = ($output -join "`n") }
}

# Sizes in GiB; fractions are exact (multiples of 1/1024 GiB), so the boundary is exact.
function New-Reading([double]$committed, [double]$limit = 95, [double]$available = 6.25) {
  @{
    CommittedBytes = [long]($committed * $gib)
    CommitLimit    = [long]($limit * $gib)
    AvailableBytes = [long]($available * $gib)
  } | ConvertTo-Json -Compress
}

function New-Events([double[]]$limits) {
  '[' + (($limits | ForEach-Object { [long]($_ * $gib) }) -join ',') + ']'
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

function Assert-Match([string]$pattern, [string]$text, [string]$what) {
  if ($text -notmatch $pattern) { throw "$what`: <$text> does not match <$pattern>" }
}

function Assert-NoMatch([string]$pattern, [string]$text, [string]$what) {
  if ($text -match $pattern) { throw "$what`: <$text> unexpectedly matches <$pattern>" }
}

$events127 = New-Events @(127.125)

Test-Case 'headroom above the minimum: exit 0, prints every value in GiB' {
  $r = Invoke-MemCheck @('-ReadingJson', (New-Reading 90), '-EventLimitsJson', $events127)
  Assert-Equal 0 $r.Code 'exit code'
  Assert-Equal 'mem-check: committed 90.0 GiB, limit 95.0 GiB, ceiling 127.13 GiB (max of 1 Event 2004 entries), headroom 37.13 GiB (min 30 GiB), available RAM 6.3 GiB: OK' $r.Output 'output'
}

Test-Case 'headroom below the minimum: exit 1' {
  $r = Invoke-MemCheck @('-ReadingJson', (New-Reading 100), '-EventLimitsJson', $events127)
  Assert-Equal 1 $r.Code 'exit code'
  Assert-Match 'headroom 27\.13 GiB .*: BUSY$' $r.Output 'output'
}

Test-Case 'headroom exactly at the minimum: exit 0; one byte less: exit 1' {
  $ceiling = New-Events @(127)
  $at = Invoke-MemCheck @('-ReadingJson', (New-Reading 97), '-EventLimitsJson', $ceiling)
  Assert-Equal 0 $at.Code 'exit code at exactly 30 GiB'
  Assert-Match 'headroom 30\.00 GiB .*: OK$' $at.Output 'output at the boundary'
  $reading = @{ CommittedBytes = (97 * $gib + 1); CommitLimit = (95 * $gib); AvailableBytes = $gib } | ConvertTo-Json -Compress
  $under = Invoke-MemCheck @('-ReadingJson', $reading, '-EventLimitsJson', $ceiling)
  Assert-Equal 1 $under.Code 'exit code one byte under 30 GiB'
}

Test-Case '-MinHeadroomGB moves the boundary both ways' {
  $relaxed = Invoke-MemCheck @('-MinHeadroomGB', '10', '-ReadingJson', (New-Reading 100), '-EventLimitsJson', $events127)
  Assert-Equal 0 $relaxed.Code 'exit code, 27 GiB headroom with min 10'
  Assert-Match '\(min 10 GiB\)' $relaxed.Output 'min printed'
  $strict = Invoke-MemCheck @('-MinHeadroomGB', '40', '-ReadingJson', (New-Reading 90), '-EventLimitsJson', $events127)
  Assert-Equal 1 $strict.Code 'exit code, 37 GiB headroom with min 40'
  $zero = Invoke-MemCheck @('-MinHeadroomGB', '0', '-ReadingJson', (New-Reading 126), '-EventLimitsJson', $events127)
  Assert-Equal 0 $zero.Code 'exit code, an explicit 0 is a minimum, not the default'
  Assert-Match '\(min 0 GiB\)' $zero.Output 'min 0 printed'
}

Test-Case 'no Event 2004 entries: ceiling falls back to CommitLimit and says so' {
  $busy = Invoke-MemCheck @('-ReadingJson', (New-Reading 175 200), '-EventLimitsJson', '[]')
  Assert-Equal 1 $busy.Code 'exit code, 25 GiB under the CommitLimit'
  Assert-Match '(?m)^mem-check: ceiling falls back to the current CommitLimit \(no Event 2004 entries\)$' $busy.Output 'fallback line'
  Assert-Match 'ceiling 200\.00 GiB \(current CommitLimit, fallback\), headroom 25\.00 GiB' $busy.Output 'ceiling is the CommitLimit'
  $ok = Invoke-MemCheck @('-ReadingJson', (New-Reading 160 200), '-EventLimitsJson', '[]')
  Assert-Equal 0 $ok.Code 'exit code, 40 GiB under the CommitLimit'
}

Test-Case 'the default minimum is 25% of the ceiling, capped at 30 GiB, and is printed' {
  $small = Invoke-MemCheck @('-ReadingJson', (New-Reading 9 38), '-EventLimitsJson', '[]')
  Assert-Equal 0 $small.Code 'exit code, an idle 32 GB machine: 29 GiB headroom against 9.5'
  Assert-Match '\(min 9\.5 GiB\)' $small.Output 'minimum printed'
  $tiny = Invoke-MemCheck @('-ReadingJson', (New-Reading 15 19), '-EventLimitsJson', '[]')
  Assert-Equal 1 $tiny.Code 'exit code, a loaded 16 GB machine: 4 GiB headroom against 4.75'
  Assert-Match '\(min 4\.75 GiB\).*: BUSY$' $tiny.Output 'minimum printed on BUSY'
  $at = Invoke-MemCheck @('-ReadingJson', (New-Reading 75 100), '-EventLimitsJson', '[]')
  Assert-Equal 0 $at.Code 'exit code, headroom exactly 25% of the ceiling'
  Assert-Match '\(min 25 GiB\)' $at.Output 'minimum below the cap'
  $capped = Invoke-MemCheck @('-ReadingJson', (New-Reading 90), '-EventLimitsJson', $events127)
  Assert-Match '\(min 30 GiB\)' $capped.Output 'the cap applies at a ceiling of 127 GiB'
  $override = Invoke-MemCheck @('-Ceiling', '40', '-ReadingJson', (New-Reading 10), '-EventLimitsJson', $events127)
  Assert-Match '\(min 10 GiB\)' $override.Output 'the minimum follows the -Ceiling override'
  $explicit = Invoke-MemCheck @('-MinHeadroomGB', '30', '-ReadingJson', (New-Reading 9 38), '-EventLimitsJson', '[]')
  Assert-Equal 1 $explicit.Code '-MinHeadroomGB replaces the computed minimum'
}

Test-Case 'an unreadable event log falls back to CommitLimit and says so' {
  $r = Invoke-MemCheck @('-ReadingJson', (New-Reading 60 100), '-EventLimitsJson', 'not json')
  Assert-Equal 0 $r.Code 'exit code'
  Assert-Match '(?m)^mem-check: ceiling falls back to the current CommitLimit \(event log read failed: .+\)$' $r.Output 'fallback line'
  Assert-Match 'ceiling 100\.00 GiB \(current CommitLimit, fallback\)' $r.Output 'ceiling is the CommitLimit'
}

Test-Case 'the ceiling is the maximum of several entries, wherever it sits' {
  foreach ($order in @(@(110, 127.125, 100), @(127.125, 100, 110), @(100, 110, 127.125))) {
    $r = Invoke-MemCheck @('-ReadingJson', (New-Reading 90), '-EventLimitsJson', (New-Events $order))
    Assert-Match 'ceiling 127\.13 GiB \(max of 3 Event 2004 entries\), headroom 37\.13 GiB' $r.Output "output for order $($order -join ',')"
    Assert-NoMatch 'falls back' $r.Output "no fallback for order $($order -join ',')"
  }
}

Test-Case '-Ceiling overrides the events, in GiB' {
  $lower = Invoke-MemCheck @('-Ceiling', '110', '-ReadingJson', (New-Reading 90), '-EventLimitsJson', $events127)
  Assert-Equal 1 $lower.Code 'exit code, override 110 under events 127 leaves 20 GiB'
  Assert-Match 'ceiling 110\.00 GiB \(-Ceiling override\), headroom 20\.00 GiB' $lower.Output 'lower override output'
  $higher = Invoke-MemCheck @('-Ceiling', '127', '-ReadingJson', (New-Reading 90), '-EventLimitsJson', (New-Events @(100)))
  Assert-Equal 0 $higher.Code 'exit code, override 127 over events 100 leaves 37 GiB'
  Assert-Match 'ceiling 127\.00 GiB \(-Ceiling override\)' $higher.Output 'higher override output'
  $noEvents = Invoke-MemCheck @('-Ceiling', '127', '-ReadingJson', (New-Reading 90), '-EventLimitsJson', '[]')
  Assert-NoMatch 'falls back' $noEvents.Output 'an override needs no event read'
}

Test-Case 'an unusable CIM reading fails closed with exit 2, never as free' {
  $missing = Invoke-MemCheck @('-ReadingJson', '{"CommittedBytes":1,"CommitLimit":2}', '-EventLimitsJson', $events127)
  Assert-Equal 2 $missing.Code 'exit code, missing AvailableBytes'
  Assert-Match 'no reading \(missing AvailableBytes\)' $missing.Output 'output'
  $junk = Invoke-MemCheck @('-ReadingJson', 'not json', '-EventLimitsJson', $events127)
  Assert-Equal 2 $junk.Code 'exit code, not JSON'
  $text = Invoke-MemCheck @('-ReadingJson', '{"CommittedBytes":"1","CommitLimit":2,"AvailableBytes":3}', '-EventLimitsJson', $events127)
  Assert-Equal 2 $text.Code 'exit code, a number given as text'
  $zero = Invoke-MemCheck @('-ReadingJson', '{"CommittedBytes":1,"CommitLimit":0,"AvailableBytes":3}', '-EventLimitsJson', $events127)
  Assert-Equal 2 $zero.Code 'exit code, CommitLimit 0'
  $empty = Invoke-MemCheck @('-ReadingJson', '', '-EventLimitsJson', $events127)
  Assert-Equal 2 $empty.Code 'exit code, empty -ReadingJson'
}

Test-Case 'numbers use the invariant culture (a dot, not a locale comma)' {
  $r = Invoke-MemCheck @('-MinHeadroomGB', '2.5', '-ReadingJson', (New-Reading 40.5 95 0.5), '-EventLimitsJson', $events127)
  Assert-Match 'committed 40\.5 GiB, .*\(min 2\.5 GiB\), available RAM 0\.5 GiB: OK$' $r.Output 'output'
}

Test-Case 'out-of-range parameters are refused, not run' {
  foreach ($bad in @(@('-Ceiling', '0'), @('-MinHeadroomGB', '-1'))) {
    $r = Invoke-MemCheck ($bad + @('-ReadingJson', (New-Reading 10), '-EventLimitsJson', $events127))
    Assert-Equal $true ($r.Code -ne 0) "exit code for $($bad -join ' ') is non-zero"
    Assert-NoMatch 'mem-check: committed' $r.Output "no verdict printed for $($bad -join ' ')"
  }
}

Test-Case 'live read: committed and ceiling match an independent CIM and Event 2004 read' {
  $r = Invoke-MemCheck @()
  $cim = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
  $limits = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 2004 } -ErrorAction SilentlyContinue |
      ForEach-Object { [long]([xml]$_.ToXml()).Event.UserData.MemoryExhaustionInfo.SystemInfo.SystemCommitLimit })
  $ceilingBytes = if ($limits.Count -gt 0) { ($limits | Measure-Object -Maximum).Maximum } else { $cim.CommitLimit }
  $min = [math]::Min([double]30, 0.25 * $ceilingBytes / $gib)
  $m = [regex]::Match($r.Output, 'committed ([\d.]+) GiB, limit ([\d.]+) GiB, ceiling ([\d.]+) GiB \(([^)]+)\), headroom (-?[\d.]+) GiB \(min ([\d.]+) GiB\), available RAM ([\d.]+) GiB: (OK|BUSY)$')
  Assert-Equal $true $m.Success "output shape <$($r.Output)>"
  $g = $m.Groups
  $num = { param($t) [double]::Parse($t, $inv) }
  Assert-Equal ($ceilingBytes / $gib).ToString('0.00', $inv) $g[3].Value 'ceiling'
  Assert-Equal ($cim.CommitLimit / $gib).ToString('0.0', $inv) $g[2].Value 'limit'
  Assert-Equal $true ([math]::Abs((& $num $g[1].Value) - $cim.CommittedBytes / $gib) -lt 1.0) 'committed within 1 GiB of the CIM read'
  Assert-Equal $min.ToString('0.##', $inv) $g[6].Value 'minimum'
  Assert-Equal $true ([math]::Abs((& $num $g[7].Value) - $cim.AvailableBytes / $gib) -lt 1.0) 'available within 1 GiB of the CIM read'
  Assert-Equal $true ([math]::Abs((& $num $g[5].Value) - ((& $num $g[3].Value) - (& $num $g[1].Value))) -lt 0.1) 'headroom = ceiling - committed'
  Assert-Equal ([int]((& $num $g[5].Value) -lt $min)) $r.Code 'exit code for the printed headroom'
}

Write-Host ''
Write-Host "mem-check tests: $passed passed, $($failures.Count) failed."
if ($failures.Count -gt 0) { $failures | ForEach-Object { Write-Host "  $_" }; exit 1 }
exit 0
