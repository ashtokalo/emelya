---
description: Working with git repositories
---
# Git

## Viewing state

Use bash for git commands:
- `git status` — current state
- `git log --oneline -20` — recent commits
- `git diff` — unstaged changes
- `git diff --staged` — staged changes
- `git branch -a` — all branches

## Making changes

1. Check status: `git status`
2. Stage files: `git add <files>`
3. Commit: `git commit -m "description"`

## Investigating history

- `git log --oneline --all --graph -30` — visual history
- `git show <commit>` — commit details
- `git blame <file>` — line-by-line authorship
- `git log -p -- <file>` — file change history

## Common tasks

Undo last commit (keep changes): `git reset --soft HEAD~1`
Discard unstaged changes: `git checkout -- <file>`
Create branch: `git checkout -b <name>`
Stash changes: `git stash` / `git stash pop`