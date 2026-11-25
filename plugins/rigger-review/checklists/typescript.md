# TypeScript and JavaScript checklist

- `await` inside `forEach` does not wait: the loop returns before the work is done. Use
  `for … of` or `Promise.all`.
- `Array.prototype.sort()` without a comparator sorts as strings (`[10, 9, 1]` becomes
  `[1, 10, 9]`).
- `x || fallback` replaces `0`, `""` and `false`; `x ?? fallback` replaces only `null` and
  `undefined`. Check which one the value allows.
- `JSON.parse(text) as T` checks nothing at run time: outside input is validated before it is
  used as `T`.
- Ids above 2^53 lose digits when parsed as numbers from JSON; they stay strings.
- `new Date("2026-01-02")` is UTC midnight, `new Date(2026, 0, 2)` is local, and months count from
  0.
- `Promise.all` rejects on the first failure while the other promises keep running; work that must
  finish or be cancelled uses `allSettled` or an `AbortController`.
- A regex with the `g` or `y` flag keeps `lastIndex` between `.test()` calls on different strings.
- `for … in` over an array walks string keys and inherited properties.
- Object spread and `Object.assign` copy one level: nested state changed in place still mutates
  the original (stale renders, shared defaults).
- Merging outside objects into a plain object lets a `__proto__` or `constructor` key pollute
  every object: use `Object.create(null)`, a `Map` or a key allow-list.
- A `useEffect` that subscribes, starts a timer or fetches returns a cleanup, and a late response
  cannot overwrite a newer one (an ignore flag or an abort).
- `parseInt` without a radix, and `Number("")` being `0`, turn bad input into a valid-looking
  number.
- `typeof null === "object"`: an object check also tests for `null`.
