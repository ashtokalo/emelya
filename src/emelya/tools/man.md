# man

Show detailed help for any tool, including parameters, defaults, and examples.

## Parameters

- `tool` (string, required) — Name of the tool to get help for.

## Examples

man(tool="read")
man(tool="grep")
man(tool="glob")

## Notes

- Looks up the .md manual file next to the tool's .py file.
- Also resolves short names (e.g. `"glob"` finds `glob_tool.md`).
- Lists available tools if the requested one is not found.
