---
description: Review a file for bugs, issues and improvements
tools: read
max_steps: 3
---
Review the file `{path}` for code quality.

1. Use read to see the full file (or first 200 lines if large)
2. If the file is longer than 200 lines, read the remaining parts

Look for:
- Bugs: logic errors, off-by-one, null/undefined access, resource leaks
- Security: injection, hardcoded secrets, unsafe input handling
- Error handling: missing try/catch, swallowed exceptions
- Performance: obvious inefficiencies, N+1 patterns
- Style: inconsistencies, dead code, overly complex logic

Return:
- List of issues found, each with: severity (bug/warning/note), line number, description
- If no issues found, say so
- Keep it concise — only real issues, not style nitpicks