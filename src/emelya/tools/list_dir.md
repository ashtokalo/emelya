# list_dir

List the contents of a directory.

## Parameters

- `path` (string, optional) — Directory to list. Default: `"."` (current directory).
- `show_hidden` (bool, optional) — Include hidden files and directories. Default: false.

## Examples

list_dir()
list_dir(path="/home/user/projects")
list_dir(path="src", show_hidden=true)

## Notes

- Directories are shown first, suffixed with `/`.
- Files are shown with their size.
- A summary line is printed at the end.
