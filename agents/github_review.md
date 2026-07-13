---
description: Review a GitHub PR — fetches diff and metadata via gh CLI, analyzes changes
tools: bash, grep, read
max_steps: 15
---

Review GitHub Pull Request. Args: {pr} (PR number, URL, or empty for current branch's PR).

## Step 1 — Get PR metadata

```bash
gh pr view {pr} --json number,title,body,author,baseRefName,headRefName,additions,deletions,changedFiles,files
```

If `pr` is empty, omit the argument (reviews the PR for the current branch).
From the output, note: title, description, changed file list, total additions/deletions.

## Step 2 — Get the full diff

```bash
gh pr diff {pr}
```

Read the diff carefully. Identify:
- Which files changed and how significantly
- What the overall intent of the changes is

## Step 3 — Read context for key files

For files with substantial logic changes (skip lock files, generated files, changelogs, translations):
- Use `read` to see the surrounding code, not just the diff
- Focus on: function signatures, error handling, data flow, edge cases

## Step 4 — Analyze

Check for:

**Correctness**
- Logic errors, wrong conditions, off-by-one
- Missing null/undefined/empty checks
- Incorrect error handling or swallowed exceptions
- Race conditions or concurrency issues

**Security**
- SQL/command/path injection
- Hardcoded secrets or tokens
- Unsafe deserialization
- Missing auth/permission checks
- Path traversal in file operations

**Tests**
- Are new code paths covered by tests?
- Are existing tests updated to match changes?
- Are edge cases tested?

**Breaking changes**
- Changed function signatures, removed exports
- Database schema changes without migration
- Config key renames or removals

**Performance**
- N+1 queries, unnecessary loops
- Missing indexes for new query patterns
- Large allocations in hot paths

## Return format

**PR**: #{number} — {title}
**Author**: {author} | **Branch**: {head} → {base}
**Changes**: +{additions} -{deletions} across {n} files

**Summary**: what this PR does (2-3 sentences)

**Issues** (list only real problems, skip nitpicks):
- `[critical|warning|note]` `file.py:42` — description

**Verdict**: ✅ Looks good / ⚠️ Minor issues / ❌ Needs changes
