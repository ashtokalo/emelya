---
description: Find files related to a given file (imports and dependents)
tools: grep, read
max_steps: 5
---
Find all files related to `{path}`.

1. Use read to see the imports/includes at the top of the file (first 30 lines)
2. Extract module/file names from imports
3. Use grep to find other files that import or reference this file's name

Return:
- **Imports**: what this file depends on (list of files/modules)
- **Imported by**: what files depend on this one (list of files with line numbers)
- Brief description of the dependency relationship