<#
.SYNOPSIS
  Commit-charge gate: headroom = ceiling - committed; exits 1 when it is below the minimum.

.DESCRIPTION
  committed, current limit and available RAM come from Win32_PerfFormattedData_PerfOS_Memory
  through Get-CimInstance (CommittedBytes, CommitLimit, AvailableBytes). Get-Counter is not used:
  on a Windows with a non-English locale it fails with error c0000bb8.

  The ceiling is the largest SystemCommitLimit among the System-log Event 2004 entries
  (Resource-Exhaustion-Detector), because the system-managed pagefile grows and shrinks, so the
  current CommitLimit under-states what the machine reached when it last ran out. With no entries,
  or a read error, the ceiling falls back to the current CommitLimit and the output says so.
  -Ceiling (GiB) overrides both.

  The minimum is min(30 GiB, 25% of the ceiling), so a machine with a small commit limit can pass;
  -MinHeadroomGB replaces it. The line printed names the minimum used.

  Every value is GiB (1024^3 bytes), printed with the invariant culture.

  Exit codes: 0 = headroom at or above the minimum, 1 = below it (wait and re-check),
  2 = no usable CIM reading (fail closed: never read as free).

  -ReadingJson (the three CIM fields) and -EventLimitsJson (a JSON array of SystemCommitLimit
  bytes) are the test seams.

.EXAMPLE
  pwsh -NoProfile -File <rigger-windows root>/tools/mem-check/mem-check.ps1
#>
param(
  [ValidateRange(0, 1024)][double]$MinHeadroomGB,
  [ValidateRange(1, 4096)][double]$Ceiling,
  [string]$ReadingJson,
  [string]$EventLimitsJson
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$fields = 'CommittedBytes', 'CommitLimit', 'AvailableBytes'
$inv = [Globalization.CultureInfo]::InvariantCulture
$gib = [double]1GB
$eventSeam = $PSBoundParameters.ContainsKey('EventLimitsJson')

function Stop-NoReading([string]$why) {
  Write-Host "mem-check: no reading ($why)"
  exit 2
}

function Format-Gib([double]$bytes, [string]$format = '0.0') {
  ($bytes / $gib).ToString($format, $inv) + ' GiB'
}

# Returns the SystemCommitLimit (bytes) of every Event 2004 entry; throws on any unreadable one.
function Get-EventCommitLimits {
  if ($script:eventSeam) {
    return @($script:EventLimitsJson | ConvertFrom-Json | ForEach-Object { [long]$_ })
  }
  # No entries makes Get-WinEvent throw; the caller falls back like for any read error.
  $events = Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 2004 } -ErrorAction Stop
  foreach ($e in $events) {
    [long](([xml]$e.ToXml()).GetElementsByTagName('SystemCommitLimit')[0].InnerText)
  }
}

try {
  $reading = if ($PSBoundParameters.ContainsKey('ReadingJson')) {
    $ReadingJson | ConvertFrom-Json
  }
  else {
    Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
  }
}
catch { Stop-NoReading $_.Exception.Message }

if ($null -eq $reading) { Stop-NoReading 'empty' }
foreach ($f in $fields) {
  if ($null -eq $reading.PSObject.Properties[$f]) { Stop-NoReading "missing $f" }
  $v = $reading.$f
  if ($null -eq $v -or $v -isnot [ValueType] -or [double]$v -lt 0) { Stop-NoReading "bad $f" }
}
if ([double]$reading.CommitLimit -le 0) { Stop-NoReading 'CommitLimit is 0' }

$committed = [double]$reading.CommittedBytes
$limit = [double]$reading.CommitLimit

$fallback = $null
if ($PSBoundParameters.ContainsKey('Ceiling')) {
  $ceilingBytes = $Ceiling * $gib
  $source = '-Ceiling override'
}
else {
  try {
    $limits = @(Get-EventCommitLimits)
    if ($limits.Count -eq 0) { $fallback = 'no Event 2004 entries' }
  }
  catch {
    $limits = @()
    $fallback = "event log read failed: $($_.Exception.Message)"
  }
  if ($limits.Count -gt 0) {
    $ceilingBytes = [double]($limits | Measure-Object -Maximum).Maximum
    $source = "max of $($limits.Count) Event 2004 entries"
  }
  else {
    $ceilingBytes = $limit
    $source = 'current CommitLimit, fallback'
  }
}

if (-not $PSBoundParameters.ContainsKey('MinHeadroomGB')) {
  $MinHeadroomGB = [math]::Min([double]30, 0.25 * $ceilingBytes / $gib)
}

$headroom = $ceilingBytes - $committed
$busy = $headroom -lt ($MinHeadroomGB * $gib)
$state = if ($busy) { 'BUSY' } else { 'OK' }

if ($fallback) { Write-Host "mem-check: ceiling falls back to the current CommitLimit ($fallback)" }
Write-Host ("mem-check: committed $(Format-Gib $committed), limit $(Format-Gib $limit), " +
  "ceiling $(Format-Gib $ceilingBytes '0.00') ($source), headroom $(Format-Gib $headroom '0.00') " +
  "(min $($MinHeadroomGB.ToString('0.##', $inv)) GiB), available RAM $(Format-Gib ([double]$reading.AvailableBytes)): $state")
if ($busy) { exit 1 }
exit 0
