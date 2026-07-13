# load_tools

Load extension tools by category.

## Parameters

- `category` (string, required) — The category of tools to load.

## Examples

load_tools(category="image")
load_tools(category="database")

## Notes

- Categories are discovered from tool modules that have a `CATEGORY` attribute.
- MCP servers are also available as categories.
