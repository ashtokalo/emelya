"""Hooks system — pre/post event hooks with command, http, and prompt support."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class HookResult:
    """Result returned by a hook invocation."""
    block: bool = False
    reason: str = ""
    message: str = ""
    context: str = ""
    result: Optional[str] = None   # None = don't replace output
    command: Optional[str] = None  # None = don't replace command (PreBash only)


class HookRunner:
    """Loads and runs hooks from global and local hooks.json files.

    Hooks are loaded from:
      1. ~/.emelya/hooks.json  (global)
      2. .emelya/hooks.json    (local, extends global)

    For each event the lists are concatenated: global hooks run first.
    """

    def __init__(self, work_dir: Path, llm_getter: Callable = None):
        """
        Args:
            work_dir: Project working directory (for local hooks.json).
            llm_getter: Callable(model_name) -> LLMClient for prompt hooks.
        """
        self.work_dir = work_dir
        self._llm_getter = llm_getter
        self._hooks = self._load_hooks()

    def _load_hooks(self) -> dict:
        """Load and merge global + local hooks.json.

        Per-event lists are concatenated: global hooks first, local appended.
        """
        merged: dict[str, list] = {}
        global_path = Path.home() / ".emelya" / "hooks.json"
        local_path = self.work_dir / ".emelya" / "hooks.json"

        for path in (global_path, local_path):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                for event, hooks in data.items():
                    if not isinstance(hooks, list):
                        continue
                    if event not in merged:
                        merged[event] = []
                    merged[event].extend(hooks)
            except Exception:
                pass

        return merged

    def run(self, event: str, data: dict) -> HookResult:
        """Run all hooks for the event. Returns combined HookResult.

        Chain behaviour: command/result substitutions from one hook update
        ``current_data`` so subsequent hooks in the chain receive the
        already-modified values.
        """
        hooks = self._hooks.get(event, [])
        if not hooks:
            return HookResult()

        current_data = dict(data)
        final = HookResult()

        for hook_cfg in hooks:
            # Optional tools filter — skip hook if tool name doesn't match
            tools_filter = hook_cfg.get("tools")
            if tools_filter:
                if current_data.get("tool") not in tools_filter:
                    continue

            try:
                hr = self._run_one(hook_cfg, current_data, event)
            except Exception:
                continue

            # block is sticky — once set, stays set
            if hr.block:
                final.block = True
                if hr.reason:
                    final.reason = hr.reason

            if hr.message:
                final.message = hr.message

            if hr.context:
                final.context = (final.context + "\n" + hr.context).strip() if final.context else hr.context

            # Propagate command substitution (PreBash only)
            if hr.command is not None:
                final.command = hr.command
                current_data["command"] = hr.command

            # Propagate result substitution
            if hr.result is not None:
                final.result = hr.result
                # Update current_data so next hook sees the substituted value
                if "stdout" in current_data:
                    current_data["stdout"] = hr.result
                elif "result" in current_data:
                    current_data["result"] = hr.result

            if final.block:
                break

        return final

    # ------------------------------------------------------------------
    # Internal dispatchers
    # ------------------------------------------------------------------

    def _run_one(self, hook_cfg: dict, data: dict, event: str) -> HookResult:
        hook_type = hook_cfg.get("type", "command")
        timeout = int(hook_cfg.get("timeout", 10))

        if hook_type == "command":
            return self._run_command(hook_cfg, data, event, timeout)
        if hook_type == "http":
            return self._run_http(hook_cfg, data, timeout)
        if hook_type == "prompt":
            return self._run_prompt(hook_cfg, data, event, timeout)
        return HookResult()

    def _parse_hook_response(self, text: str) -> HookResult:
        """Parse a JSON hook response into a HookResult. Ignores unknown fields."""
        hr = HookResult()
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return hr

        hr.block = bool(parsed.get("block", False))
        hr.reason = str(parsed.get("reason", ""))
        hr.message = str(parsed.get("message", ""))
        hr.context = str(parsed.get("context", ""))

        result_val = parsed.get("result")
        if result_val is not None:
            hr.result = str(result_val)

        cmd_val = parsed.get("command")
        if cmd_val is not None:
            hr.command = str(cmd_val)

        return hr

    # ------------------------------------------------------------------
    # Hook type implementations
    # ------------------------------------------------------------------

    def _run_command(self, cfg: dict, data: dict, event: str, timeout: int) -> HookResult:
        """Run a command hook.

        stdin  = JSON-encoded data dict
        stdout = JSON-encoded HookResult fields (optional)
        exit 2 = block the event (even without JSON output)
        """
        cmd = cfg.get("command")
        if not cmd:
            return HookResult()

        env = os.environ.copy()
        env["EMELYA_EVENT"] = event
        env["EMELYA_WORK_DIR"] = str(self.work_dir)
        if "tool" in data:
            env["EMELYA_TOOL"] = str(data["tool"])
        if "command" in data:
            env["EMELYA_COMMAND"] = str(data["command"])

        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                input=json.dumps(data),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.work_dir),
                env=env,
            )
        except subprocess.TimeoutExpired:
            return HookResult()
        except Exception:
            return HookResult()

        hr = HookResult()
        if proc.returncode == 2:
            hr.block = True

        output = (proc.stdout or "").strip()
        if output:
            parsed = self._parse_hook_response(output)
            # Merge: exit-code-2 block wins even if JSON says block=false
            parsed.block = parsed.block or hr.block
            return parsed

        return hr

    def _run_http(self, cfg: dict, data: dict, timeout: int) -> HookResult:
        """Run an HTTP hook — POST JSON payload to ``url``."""
        import urllib.request

        url = cfg.get("url")
        if not url:
            return HookResult()

        allowed_vars: list = cfg.get("allowedEnvVars", [])

        def _interpolate(s: str) -> str:
            """Replace ${VAR} placeholders, only for vars in allowedEnvVars."""
            def _repl(m: re.Match) -> str:
                var = m.group(1)
                if var in allowed_vars:
                    return os.environ.get(var, "")
                return m.group(0)
            return re.sub(r"\$\{([^}]+)\}", _repl, s)

        headers = {"Content-Type": "application/json"}
        for k, v in cfg.get("headers", {}).items():
            headers[k] = _interpolate(str(v))

        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8").strip()
        except Exception:
            return HookResult()

        if body:
            return self._parse_hook_response(body)
        return HookResult()

    def _run_prompt(self, cfg: dict, data: dict, event: str, timeout: int) -> HookResult:
        """Run a prompt hook — isolated LLM call with no main-context side-effects."""
        if not self._llm_getter:
            return HookResult()

        prompt_template = cfg.get("prompt", "")
        if not prompt_template:
            return HookResult()

        model_name = cfg.get("model", "default")
        client = self._llm_getter(model_name)
        if not client:
            return HookResult()

        # Replace {event} and all data keys in the prompt template
        prompt = prompt_template.replace("{event}", event)
        for k, v in data.items():
            prompt = prompt.replace("{" + k + "}", str(v))

        prompt += (
            "\n\nRespond with JSON only, no markdown fences:\n"
            '{"block": bool, "reason": "str", "message": "str", "context": "str", "result": null_or_str}'
        )

        messages = [{"role": "user", "content": prompt}]
        try:
            response = client.chat(
                messages=messages,
                tools=None,
                temperature=0.0,
                max_tokens=512,
            )
            text = (response.get("content") or "").strip()
        except Exception:
            return HookResult()

        # Extract JSON from response (model may add prose before/after)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return self._parse_hook_response(match.group())
        return HookResult()
