# PHP checklist

- Loose comparison: `"1e3" == "1000"` is true, and `in_array` and `array_search` compare loosely
  unless their strict argument is `true`.
- `strpos` and `array_search` return `0` for a match at the start: compare with `!== false`.
- `empty("0")` is true: a field holding `"0"` reads as missing.
- `array_merge` renumbers integer keys, `+` keeps the left side's: ids used as keys get lost or
  shadowed.
- `foreach ($items as &$item)` leaves `$item` a reference after the loop; a later loop over the
  same name overwrites the last element. `unset($item)` after it.
- `json_decode` returns `null` on bad input unless `JSON_THROW_ON_ERROR` is set, and objects unless
  its second argument is `true`.
- `preg_match` returns `false` on an engine error (backtrack limit, bad UTF-8), which a plain
  truthiness check reads as "no match".
- `DateTime` methods change the object in place: a value shared with a caller moves too. Prefer
  `DateTimeImmutable`.
- `unserialize` on outside input can build objects and run their magic methods: use `json_decode`
  or `allowed_classes => false`.
- `filter_var($v, FILTER_VALIDATE_BOOLEAN)` returns `false` for garbage unless
  `FILTER_NULL_ON_FAILURE` is set.
- `header('Location: …')` without `exit` keeps running the rest of the script.
- `$_REQUEST` mixes query, body and cookies: a value meant from the body can come from a cookie.
- `intval` and `(int)` clamp or truncate large or non-numeric strings silently.
- `extract()` or variable variables on request data overwrite local variables.
