---
description: Explain what a piece of code does (function, class, or file section)
tools: grep, read
max_steps: 4
---
Explain the code: `{target}` in the project.

1. Use grep to find where `{target}` is defined
2. Use read to see the full implementation (with surrounding context)
3. If it calls other functions, use grep to find their definitions if needed

Return:
- What the code does (plain language, 2-4 sentences)
- Input/output: what it takes and what it returns
- Key logic: step-by-step walkthrough of the algorithm
- Side effects: files, network, state changes
- Edge cases or error handling worth noting