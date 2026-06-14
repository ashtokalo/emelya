---
description: Detect language and run appropriate linter to find syntax and style errors
tools: bash, read, glob
max_steps: 6
---
Lint-check the file or directory: `{target}`

Steps:

1. Determine the language:
   - If target is a file, check its extension
   - If target is a directory, use glob to find source files and determine the primary language

2. Run the appropriate linter (try in order, use first available):

   **Python**: `ruff check {target}` → `flake8 {target}` → `python3 -m py_compile {target}`
   **JavaScript/TypeScript**: `npx eslint {target}` → `node --check {target}` (syntax only)
   **PHP**: `php -l {target}` (syntax) → `phpstan analyse {target}` → `phpcs {target}`
   **Go**: `go vet {target}` → `staticcheck {target}`
   **C/C++**: `gcc -fsyntax-only -Wall -Wextra {target}` or `g++ -fsyntax-only -Wall -Wextra {target}`
   **Rust**: `cargo clippy` (from project root) → `rustc --edition 2021 --crate-type lib {target}` (syntax)
   **Bash/Shell**: `shellcheck {target}` → `bash -n {target}` (syntax only)
   **HTML**: `python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('{target}').read())"` (basic parse check)
   **CSS**: check for common syntax errors with grep patterns

3. Parse the linter output and categorize findings

Return:
- Language detected
- Linter used
- List of issues: file:line, severity (error/warning), message
- Total count: errors vs warnings
- If clean, say "No issues found"