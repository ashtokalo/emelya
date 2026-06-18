# grep

Search file contents using a regular expression pattern.

## Parameters

- `pattern` (string, required) — Regular expression to search for.
- `path` (string, optional) — File or directory to search in. Defaults to the current working directory.
- `include` (string, optional) — Glob filter for filenames, e.g. `"*.py"`.
- `ignore_case` (bool, optional) — Perform case-insensitive matching.
- `context` (int, optional) — Number of lines to show before and after each match.
- `max_results` (int, optional) — Maximum number of matches to return. Default: 50.

## Examples

grep(pattern="def main")
grep(pattern="TODO", path="src/", include="*.py")
grep(pattern="error", ignore_case=true, context=3)

## Notes

- Skips binary files automatically.
- Excludes the `.git` directory.
