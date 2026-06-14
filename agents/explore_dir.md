---
description: Explore directory structure, detect tech stack and entry points
tools: glob, read
max_steps: 6
---
Explore the directory `{path}` and describe its structure.

1. Use glob to list all files: pattern="{path}/**/*"
2. Identify the project type by key files:
   - Python: setup.py, pyproject.toml, requirements.txt
   - JavaScript: package.json, tsconfig.json
   - PHP: composer.json
   - Go: go.mod
   - Rust: Cargo.toml
   - Docker: Dockerfile, docker-compose.yml
3. Read README or main entry point if they exist (first 50 lines)
4. Read project config file if found (package.json, pyproject.toml, etc.)

Return:
- Project type and language
- Directory tree (top 2 levels, summarize deeper)
- Purpose of main directories
- Entry points (main files, scripts)
- Key dependencies if visible from config
- Total file count by extension