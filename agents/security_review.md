---
description: Security-focused review of code or changes
tools: bash, grep, read
max_steps: 8
---
Perform a security review of: {target}

If target is a file path, review that file. If "staged", "uncommitted", or a branch name, review the git diff (same as code_review).

Steps:

1. Get the code to review:
   - File: use read
   - Diff: use bash with git diff

2. Search for common vulnerability patterns:
   - `grep -rn "exec\|system\|popen\|eval\|subprocess" <files>` — command injection
   - `grep -rn "SELECT.*%s\|SELECT.*format\|SELECT.*\+" <files>` — SQL injection
   - `grep -rn "innerHTML\|document.write\|v-html" <files>` — XSS
   - `grep -rn "password\|secret\|token\|api_key" <files>` — hardcoded secrets
   - `grep -rn "chmod 777\|0777\|world-writable" <files>` — permission issues
   - `grep -rn "http://\|verify=False\|InsecureRequestWarning" <files>` — insecure connections

3. Check for:
   - Input validation on user-facing endpoints
   - Authentication/authorization gaps
   - Path traversal (../ in file operations)
   - Deserialization of untrusted data (pickle, yaml.load, unserialize)
   - Race conditions in file operations
   - Information leakage in error messages

Return:
- List of findings: severity (critical/high/medium/low), file:line, description, remediation
- If no issues found, confirm the code looks secure
- Note any areas that need manual review (crypto, auth logic)