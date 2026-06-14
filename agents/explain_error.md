---
description: Analyze an error or traceback, find the source and suggest a fix
tools: grep, read
max_steps: 5
---
Analyze this error:

```
{error}
```

1. Extract file paths and line numbers from the traceback
2. Use read to see the code around the error location (10 lines before and after)
3. If the error references other functions or modules, use grep to find them

Return:
- What the error means (1-2 sentences)
- The exact line causing the error and why
- A suggested fix with specific code changes
- If the error is ambiguous, list the most likely causes