---
description: Writing safe bash scripts — strict mode, shellcheck, quoting, common pitfalls
---
# Bash Scripting

## Strict mode

Always start scripts with:

```bash
#!/usr/bin/env bash
set -euo pipefail
```

- `set -e` — exit on any command failure
- `set -u` — error on undefined variables
- `set -o pipefail` — pipe fails if any command in it fails

## Validation with ShellCheck

- `shellcheck script.sh` — static analysis, catches most common bugs
- `shellcheck -x script.sh` — follow sourced files
- Fix all ShellCheck warnings before considering a script done

## Quoting rules

Always double-quote variables to prevent word splitting and globbing:
- `"$var"` not `$var`
- `"${array[@]}"` to expand arrays correctly
- `"$(command)"` not `$(command)` bare

Exceptions: inside `[[ ]]` tests, quoting is optional (but still good practice).

## Common pitfalls

- **Word splitting**: `for f in $(ls)` breaks on spaces. Use `for f in ./*` instead
- **Globbing**: unquoted `$var` expands globs. Always quote
- **Testing files**: use `[[ -f "$file" ]]` not `[ -f $file ]`
- **Command substitution in conditions**: `if command; then` not `if [ $(command) ]`
- **Arithmetic**: use `(( ))` for math, not `expr`

## Input validation patterns

```bash
# Require arguments
[[ $# -ge 1 ]] || { echo "Usage: $0 <arg>" >&2; exit 1; }

# Check file exists
[[ -f "$1" ]] || { echo "File not found: $1" >&2; exit 1; }

# Check command available
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }

# Default values
: "${TIMEOUT:=30}"
```

## Useful patterns

- `trap 'rm -f "$tmpfile"' EXIT` — cleanup on exit
- `readonly VAR="value"` — immutable variable
- `local var="value"` — function-scoped variable
- `cmd1 | cmd2 | cmd3` with `pipefail` catches failures in any stage

Use bash tool to run and test scripts. Use shellcheck via bash tool to validate. Use edit tool for script modifications.
