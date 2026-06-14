---
description: Project scaffolding — README, CHANGELOG, LICENSE, gitignore, editorconfig, CI
---
# Project Scaffolding

## README.md structure

Standard sections in order:

1. **Title** — project name, one-line description
2. **Installation** — how to install or build
3. **Usage** — basic examples, quick start
4. **Configuration** — env vars, config files (if applicable)
5. **Development** — how to set up dev environment, run tests
6. **License** — short note pointing to LICENSE file

Keep it concise. Prefer examples over long explanations.

## CHANGELOG.md

Follow [Keep a Changelog](https://keepachangelog.com/) format:

```markdown
# Changelog

## [Unreleased]

## [1.2.0] - 2025-03-15
### Added
- New feature description

### Changed
- Updated behavior

### Fixed
- Bug fix description

### Removed
- Deprecated feature removed
```

Categories: Added, Changed, Deprecated, Removed, Fixed, Security.

## LICENSE file

Common choices:
- **MIT** — permissive, short, most popular for open source
- **Apache 2.0** — permissive with patent grant
- **GPL 3.0** — copyleft, derivative works must also be GPL

Ask the user which license to use. Never pick one without asking.

## .gitignore

Check [github.com/github/gitignore](https://github.com/github/gitignore) for language-specific templates. Common entries:

```
# Build output
build/
dist/
bin/

# Dependencies
node_modules/
vendor/
.venv/

# IDE
.idea/
.vscode/
*.swp

# OS
.DS_Store
Thumbs.db

# Environment
.env
.env.local
```

## .editorconfig

```ini
root = true

[*]
indent_style = space
indent_size = 4
end_of_line = lf
charset = utf-8
trim_trailing_whitespace = true
insert_final_newline = true

[Makefile]
indent_style = tab
```

## CI patterns

Check for existing CI config: `.github/workflows/`, `.gitlab-ci.yml`, `Jenkinsfile`, `.circleci/`. Read existing config before modifying. Common CI steps: install deps, lint, test, build.

Use read tool to check what project files already exist before creating new ones. Use write tool to create new files.
