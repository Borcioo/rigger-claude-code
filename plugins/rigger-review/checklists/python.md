# Python checklist

- `subprocess.run(..., timeout=...)` kills only the direct child: a grandchild that keeps the pipe
  open makes the call hang past its timeout. Look for a process-group or tree kill where children
  start children.
- `os.path.join(root, part)` drops `root` when `part` is absolute, and `..` climbs out: containment
  is checked on the joined, normalised path against the root plus a separator, never with a bare
  `startswith(root)` (`/a/b` vs `/a/bc`).
- `Path.resolve()` follows symlinks and can change a path that is later compared with an
  unresolved one; `abspath` keeps the spelling.
- `re.match` anchors only at the start, and `$` also matches before a final newline: a validator
  uses `re.fullmatch` or `\Z`.
- `json.loads` of outside input may return a list, a string or a number: the type is checked
  before `.get`, indexing or iteration.
- A generator or iterator consumed twice yields nothing the second time; `map`, `filter` and file
  objects are iterators too.
- A dict, set or list changed while it is iterated: copy first or build a new one.
- Naive and aware `datetime` values compared or subtracted raise, and a naive `now()` stored as if
  it were UTC shifts by the machine's offset.
- `round()` rounds halves to even and floats hold no exact cents: amounts use integers or
  `decimal.Decimal` with an explicit rounding mode.
- `try` blocks that wrap more than the call that can raise turn an unrelated error into the
  handled case.
- `mock.patch` targets the name where it is looked up, not where it is defined: a patch of the
  defining module leaves an imported copy unpatched, and the test passes without testing.
- `assertTrue(a, b)` passes for any truthy `a` (`b` is the message); the test meant `assertEqual`.
- Shared state written from several threads of a `ThreadPoolExecutor` without a lock.
