# mem-check — commit-headroom gate

Prints the commit charge against a ceiling and exits 1 when the headroom is below the minimum. The
`windows-dev-env` "Machine load" rule has agents run it before dispatching anything that builds or
tests.

```powershell
pwsh -NoProfile -File "<rigger-windows root>/tools/mem-check/mem-check.ps1" [-MinHeadroomGB 30] [-Ceiling <GiB>]
# mem-check: committed <n> GiB, limit <n> GiB, ceiling <n> GiB (max of <n> Event 2004 entries), headroom <n> GiB (min <n> GiB), available RAM <n> GiB: OK
```

headroom = ceiling - committed. All values are GiB (1024^3 bytes).

**Minimum.** min(30 GiB, 25% of the ceiling), printed as `(min <n> GiB)`, so a machine with a small
commit limit can pass: a ceiling of 38 GiB gives 9.5, one of 120 GiB or more gives 30.
`-MinHeadroomGB <GiB>` (0..1024) replaces it.

| Exit | Meaning |
|---|---|
| 0 | headroom at or above the minimum |
| 1 | below it: wait and re-check; after 30 minutes report it as a blocker |
| 2 | no usable CIM reading (CIM failed, a field missing or not a number, `CommitLimit` 0): never read as free |

**Ceiling.** The system-managed pagefile grows and shrinks, so the current `CommitLimit` can be lower
than what the machine reached when it last ran out. The ceiling is the largest `SystemCommitLimit`
(`([xml]$e.ToXml()).Event.UserData.MemoryExhaustionInfo.SystemInfo.SystemCommitLimit`, bytes) over
all System-log Event 2004 entries. `-Ceiling <GiB>` (1..4096) replaces it. With no entries, or if
reading them fails, the ceiling is the current `CommitLimit` and a line says it fell back. The event
log keeps entries only as long as its retention allows; once they roll off, the fallback applies.

**Reads.** `committed`, `limit` and `available RAM` come from `Win32_PerfFormattedData_PerfOS_Memory`
through `Get-CimInstance` (`CommittedBytes`, `CommitLimit`, `AvailableBytes`). `Get-Counter` is not
used: on a Windows with a non-English locale it fails with error c0000bb8. Numbers print with the invariant culture.

`-ReadingJson '<json of the three CIM fields>'` and `-EventLimitsJson '[<SystemCommitLimit bytes>, ...]'`
replace the two reads (tests only).

**Cost:** one `pwsh` start, one CIM query and one event-log read plus a `ToXml()` per entry:
median 969 ms over 10 runs (range 915-999 ms, 39 entries).

## Check after editing

```powershell
pwsh -NoProfile -File "<rigger-windows root>/tools/mem-check/mem-check.tests.ps1"   # ends "mem-check tests: <n> passed, <m> failed."
```

The repository's `scripts/run_tests.py` also runs this file; the command above runs it alone. The last case runs the live read
and checks it against an independent CIM and Event 2004 read.
