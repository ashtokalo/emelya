---
description: Find all usages of a symbol in the project
tools: grep, read
max_steps: 4
---
Find all places where `{symbol}` is used in the project.

1. Use grep to find all occurrences: pattern="{symbol}"
2. For important matches, use read to show 2-3 lines of context around the usage

Return:
- List of usages grouped by file: file path, line number, the line content
- Exclude the definition itself (but include imports)
- Total count of usages
- If not found, say so explicitly