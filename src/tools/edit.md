# edit

Find and replace a string in a file.

## Parameters

- `path` (string, required) — Path to the file to edit.
- `old_string` (string, required) — The exact text to find in the file.
- `new_string` (string, required) — The replacement text.
- `replace_all` (bool, optional) — Replace all occurrences instead of just the first. Default: false.

## Examples

edit(path="src/main.py", old_string="def old_name()", new_string="def new_name()")
edit(path="src/config.py", old_string="DEBUG = True", new_string="DEBUG = False", replace_all=true)

## Notes

- You must read the file first before editing it.
- `old_string` must be unique in the file unless `replace_all` is set to true.
- Prefer small edits under 30 lines; for larger changes use `write` instead.
