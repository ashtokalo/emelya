# write

Create a new file or overwrite an existing file with the given content.

## Parameters

- `path` (string, required) — Path to the file to create or overwrite.
- `content` (string, required) — Full text content to write to the file.

## Examples

write(path="config.yaml", content="key: value\nother: 123")
write(path="src/new_module.py", content="def hello():\n    print('hello')\n")

## Notes

- You must read a file first before overwriting it.
- Parent directories are created automatically if they do not exist.
- Overwriting an existing file requires user confirmation.
