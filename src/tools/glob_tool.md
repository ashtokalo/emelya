# glob_tool

Find files matching a glob pattern.

## Parameters

- `pattern` (string, required) — Glob pattern to match, e.g. `"**/*.py"`, `"src/**/*.js"`.
- `path` (string, optional) — Base directory to search from. Defaults to the current working directory.

## Examples

glob_tool(pattern="**/*.py")
glob_tool(pattern="*.yaml", path="/etc")
glob_tool(pattern="src/**/*.test.js")

## Notes

- Excludes `.git`, `__pycache__`, and `node_modules` directories automatically.
- Returns at most 500 results.
- Hidden files are excluded unless the pattern explicitly starts with a dot (e.g. `".*"`).
