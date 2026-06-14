---
description: Find where a symbol (function, class, variable) is defined
tools: glob, grep, read
max_steps: 6
---
Find the definition of `{symbol}` in the project.

Steps:
1. Use grep to search for definition patterns:
   - Python: "def {symbol}" or "class {symbol}"
   - JavaScript/TypeScript: "function {symbol}", "const {symbol}", "class {symbol}"
   - General: "{symbol}" with context to find the definition
2. Use read to see the context around the definition (10-20 lines)

Return:
- File path and line number
- The full definition (signature + first few lines of body)
- Brief description of what it does (2-3 sentences)

If not found, say so explicitly and suggest alternative search terms.