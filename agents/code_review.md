---
description: Review code changes (git diff) for quality, bugs and security issues
tools: bash, grep, read
max_steps: 8
---
Review code changes in the project. Target: {target}

Targets can be:
- "staged" or "uncommitted" — review current uncommitted changes
- branch name — review diff between current branch and that branch
- commit hash — review that specific commit
- "last" or "HEAD" — review the last commit

Steps:

1. Get the diff:
   - For "staged"/"uncommitted": `git diff` and `git diff --staged`
   - For branch: `git diff {target}...HEAD`
   - For "last"/"HEAD": `git diff HEAD~1`
   - If target is a commit: `git show {target}`

2. Identify changed files: `git diff --name-only <same args>`

3. For each significantly changed file, use read to see the full context around changes (not just the diff)

4. Analyze changes for:
   - **Bugs**: logic errors, edge cases, null/undefined handling, off-by-one
   - **Security**: injection (SQL, command, XSS), hardcoded secrets, unsafe input, path traversal
   - **Error handling**: missing try/catch, swallowed errors, unhelpful messages
   - **Performance**: N+1 queries, unnecessary loops, missing indexes
   - **Style**: inconsistent naming, dead code, overly complex logic

Return:
- Summary: what the changes do (2-3 sentences)
- List of issues: severity (critical/warning/note), file:line, description
- If changes look good, say so
- Keep it focused on real issues, not nitpicks