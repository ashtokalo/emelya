"""Bash tool — execute shell commands with confirmation."""

import subprocess

DEFINITION = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Execute a shell command. Use for: ls, git, running scripts, "
            "and any operation not covered by other tools. "
            "Prefer read/write/edit/glob/grep for file operations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "description": {
                    "type": "string",
                    "description": "What this command does (shown to user)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30, max: 300)",
                },
                "max_output": {
                    "type": "integer",
                    "description": "Max output chars (default: 8192). Increase if output was truncated.",
                },
            },
            "required": ["command"],
        },
    },
}

# Cache for truncated command outputs: command → full_output (LRU, max 20 entries)
_output_cache = {}
_CACHE_MAX = 20

# Commands considered read-only (auto-approved even in confirm_all mode)
READONLY_COMMANDS = {
    # filesystem — listing, viewing, searching
    "ls", "cat", "grep", "rg", "find", "fd", "pwd", "which", "file", "wc",
    "head", "tail", "sort", "uniq", "du", "df", "echo", "env", "stat",
    "date", "uname", "whoami", "hostname", "id", "tree", "realpath",
    "readlink", "basename", "dirname", "test", "true", "false",
    # checksums & encoding
    "md5sum", "sha256sum", "sha1sum", "cksum", "b2sum",
    "base64", "base32", "od", "xxd", "hexdump",
    # help & docs
    "man", "info", "help", "type", "command",
    # text processing (read-only)
    "awk", "sed -n", "cut", "tr", "paste", "column", "fmt", "fold",
    "diff", "comm", "cmp", "strings", "nl", "tac", "rev", "expand",
    # json/xml/data
    "jq", "yq", "xmllint", "csvtool",
    # process & system info
    "ps", "top -bn1", "free", "uptime", "lsof", "ss", "netstat",
    "lsb_release", "getconf", "locale", "printenv", "nproc", "lscpu",
    # git read-only
    "git status", "git log", "git diff", "git branch", "git show",
    "git remote", "git tag", "git stash list", "git blame",
    "git shortlog", "git rev-parse", "git describe", "git ls-files",
    "git ls-tree", "git cat-file", "git name-rev", "git reflog",
    "git config --get", "git config --list", "git config -l",
    # mercurial read-only
    "hg status", "hg log", "hg diff", "hg cat", "hg annotate",
    "hg blame", "hg manifest", "hg branches", "hg tags", "hg heads",
    "hg summary", "hg identify", "hg paths", "hg showconfig",
    "hg files", "hg parents", "hg tip", "hg id",
    # svn read-only
    "svn status", "svn log", "svn diff", "svn cat", "svn info",
    "svn list", "svn blame", "svn annotate", "svn proplist",
    # linters & syntax checks
    "php -l", "php --syntax-check",
    "python3 -m py_compile", "python -m py_compile",
    "python3 -m pyflakes", "python -m pyflakes", "pyflakes",
    "python3 -m pylint", "pylint", "flake8", "ruff check", "ruff",
    "mypy", "pyright", "bandit",
    "node --check", "eslint", "tsc --noEmit", "tsc -noEmit",
    "rubocop", "ruby -c",
    "shellcheck", "bash -n",
    "go vet", "golint", "staticcheck",
    "rustfmt --check", "clippy",
    "yamllint", "jsonlint", "hadolint",
    "phpstan", "phpcs", "psalm",
    # C/C++ syntax check & lint
    "gcc -fsyntax-only", "g++ -fsyntax-only",
    "clang -fsyntax-only", "clang++ -fsyntax-only",
    "gcc -Wall -Werror -fsyntax-only", "g++ -Wall -Werror -fsyntax-only",
    "clang -Wall -Werror -fsyntax-only", "clang++ -Wall -Werror -fsyntax-only",
    "cppcheck", "clang-tidy", "cpplint",
    # Ada (GNAT)
    "gnat check", "gcc -c -gnatc", "gnat ls", "gnat list",
    # version checks
    "python3 --version", "python --version", "node --version",
    "php --version", "composer --version", "ruby --version",
    "go version", "rustc --version", "cargo --version",
    "java --version", "javac --version", "gcc --version",
    "make --version", "cmake --version", "npm --version",
    # project tools (read-only)
    "op list", "op inspect", "op search",
    # GitHub CLI read-only
    "gh pr view", "gh pr diff", "gh pr list", "gh pr status", "gh pr checks",
    "gh pr review --list",
    "gh issue view", "gh issue list",
    "gh repo view", "gh repo list",
    "gh release view", "gh release list",
    "gh run view", "gh run list",
    "gh workflow view", "gh workflow list",
    "gh gist view", "gh gist list",
    "gh label list",
    "gh auth status",
}

DEFAULT_MAX_OUTPUT = 8192
HARD_MAX_OUTPUT = 512000  # absolute ceiling to prevent context blowup


def _strip_cd_prefix(command: str, work_dir: str) -> str:
    """Remove redundant 'cd <work_dir> &&' prefix from command.
    The bash tool already runs in work_dir via cwd=."""
    cmd = command.strip()
    # Match: cd /path/to/workdir && rest
    # Also match with quotes: cd "/path/to/workdir" && rest
    for prefix in (f"cd {work_dir} && ", f'cd "{work_dir}" && ',
                   f"cd {work_dir} ; ", f'cd "{work_dir}" ; ',
                   f"cd {work_dir}&&", f"cd {work_dir};"):
        if cmd.startswith(prefix):
            return cmd[len(prefix):].lstrip()
    return cmd


def _is_readonly(command: str) -> bool:
    """Check if command is read-only (safe to auto-approve)."""
    cmd = command.strip()
    # Block command chaining, substitution, and redirects BEFORE pipe split
    # Check the full command for dangerous patterns
    if any(c in cmd for c in (">", "<", "`", "$(", ";")) or "&&" in cmd or "||" in cmd:
        return False
    # Handle pipes — check first command
    # "git log | head" is readonly, "rm -f x | echo done" is not
    first_cmd = cmd.split("|")[0].strip() if "|" in cmd else cmd
    for ro in READONLY_COMMANDS:
        if first_cmd == ro or first_cmd.startswith(ro + " "):
            return True
    return False


def _needs_confirm(command: str, confirm_mode: str) -> bool:
    """Determine if command needs user confirmation."""
    if confirm_mode == "no_confirm":
        return False
    # Auto-approve readonly commands in all modes
    if _is_readonly(command):
        return False
    return True


def execute(args: dict, ctx) -> str:
    command = args.get("command", "")
    description = args.get("description", command)
    timeout = min(args.get("timeout", 30), 300)
    max_output = min(args.get("max_output", DEFAULT_MAX_OUTPUT), HARD_MAX_OUTPUT)

    if not command.strip():
        return "Error: empty command"

    # Strip redundant cd to work_dir
    command = _strip_cd_prefix(command, str(ctx.work_dir))

    # Check cache — if same command was run before and truncated,
    # serve from cache with the new max_output limit
    if command in _output_cache:
        cached_output, cached_rc = _output_cache[command]
        return _format_output(cached_output, cached_rc, max_output)

    # PreBash hook — may block or substitute the command
    hook_runner = getattr(ctx, "hook_runner", None)
    if hook_runner:
        hr = hook_runner.run("PreBash", {"command": command, "tool": "bash"})
        if hr.block:
            return f"Blocked by hook: {hr.reason or hr.message or 'hook'}"
        if hr.command is not None:
            command = hr.command

    confirm_mode = getattr(ctx.config, "confirm_mode", "confirm_all") if ctx.config else "confirm_all"

    if _needs_confirm(command, confirm_mode) and ctx.confirm:
        if not ctx.confirm(f"bash: {description}", f"$ {command}"):
            return "Cancelled by user"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ctx.work_dir),
        )
        output = result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr

        output = output.strip()

        # PostBash hook — may substitute the output
        if hook_runner:
            hr = hook_runner.run("PostBash", {
                "command": command,
                "stdout": output,
                "returncode": result.returncode,
                "tool": "bash",
            })
            if hr.result is not None:
                return hr.result

        # Cache if output exceeds requested limit (may be re-requested with larger limit)
        if len(output) > max_output:
            # Evict oldest entry if cache is full
            if len(_output_cache) >= _CACHE_MAX:
                _output_cache.pop(next(iter(_output_cache)))
            _output_cache[command] = (output[:HARD_MAX_OUTPUT], result.returncode)

        return _format_output(output, result.returncode, max_output)

    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


def _format_output(output: str, returncode: int, max_output: int) -> str:
    """Format command output with truncation."""
    if not output:
        prefix = f"Exit code {returncode}\n" if returncode != 0 else ""
        return f"{prefix}(no output)" if not prefix else prefix.strip()

    total_len = len(output)
    if total_len > max_output:
        output = output[:max_output]
        output += (
            f"\n[truncated: showing {max_output} of {total_len} chars. "
            f"Use max_output={min(total_len, HARD_MAX_OUTPUT)} to see more]"
        )

    if returncode != 0:
        return f"Exit code {returncode}\n{output}"

    return output
