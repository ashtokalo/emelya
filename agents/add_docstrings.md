---
description: Add docstrings to functions and classes in a file
tools: read, edit
max_steps: 4
---
Add docstrings to all functions and classes in `{path}` that don't have them.

1. Use read to see the full file
2. Use one edit call with a `changes` array to add the required docstrings

Docstring rules:
- Use the language's standard format (Python: triple quotes, JS: JSDoc, PHP: PHPDoc)
- First line: what the function does (imperative mood, one sentence)
- Add @param / :param lines only if parameters are not self-explanatory
- Add @return / :return only if return value is not obvious
- Keep docstrings concise — 1-3 lines maximum
- Do NOT add docstrings to trivial methods (getters, setters, __init__ with obvious assignments)
