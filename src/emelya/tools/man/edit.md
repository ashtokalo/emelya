# edit

Atomically apply one or more exact text changes.

## Parameter

- `changes` (array, required) — Ordered list of file operations.

Each operation contains exactly one path key:

- `update` — existing file, exact replacement;
- `write` — existing file, complete overwrite;
- `create` — new file only;
- `delete` — existing file removal.

Paths may be relative to the work directory or absolute paths inside it. Paths
that resolve outside the sandbox are rejected.

Update fields:

- `old_string` — exact text that must occur once;
- `new_string` — replacement text.

Write/create fields:

- `content` — complete content for a new file.

## Example

```json
{
  "changes": [
    {
      "update": "src/config.py",
      "old_string": "TIMEOUT = 30",
      "new_string": "TIMEOUT = 45"
    },
    {
      "create": "tests/test_config.py",
      "content": "def test_timeout():\n    assert True\n"
    },
    {
      "delete": "src/obsolete.py"
    }
  ]
}
```

## Notes

- Read every existing file before updating, overwriting, or deleting it.
- All paths and replacements are validated before any file changes.
- Multiple updates to one file are applied sequentially in array order.
- The complete call is one undo operation.
- If commit fails, already changed files are restored.
