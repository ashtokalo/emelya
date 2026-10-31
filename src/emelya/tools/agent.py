"""Agent tool — run sub-agents as isolated inference cycles."""

import json
import copy
from pathlib import Path


DEFINITION = {
    "type": "function",
    "function": {
        "name": "agent",
        "description": "Run a sub-agent for a focused task. Returns only the result.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Agent name (e.g. 'find_definition', 'summarize_file')",
                },
                "args": {
                    "type": "object",
                    "description": "Arguments for the agent",
                },
            },
            "required": ["name", "args"],
        },
    },
}


def scan_agents(dirs) -> dict:
    """Scan one or more agents directories, return {name: description}.

    Accepts a single Path or a list of Paths. When multiple directories are
    given, later entries override earlier ones for the same agent name
    (builtin → global → local priority).
    """
    if isinstance(dirs, Path):
        dirs = [dirs]
    agents = {}
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                content = f.read_text(encoding="utf-8")
                meta, _ = _parse_agent(content)
                agents[f.stem] = meta.get("description", f.stem)
            except Exception:
                agents[f.stem] = f.stem
    return agents


def _parse_agent(content: str) -> tuple:
    """Parse agent file. Returns (meta_dict, body_str)."""
    meta = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip()
                    if key == "tools":
                        meta[key] = [v.strip() for v in value.split(",") if v.strip()]
                    elif key == "max_steps":
                        meta[key] = int(value)
                    else:
                        meta[key] = value
            body = parts[2].strip()
    return meta, body


def execute(args: dict, ctx) -> str:
    name = args.get("name", "")
    agent_args = args.get("args", {})
    # Some providers return args as string instead of dict
    if isinstance(agent_args, str):
        try:
            agent_args = json.loads(agent_args)
        except (json.JSONDecodeError, ValueError):
            agent_args = {}

    dirs = ctx.agents_dirs or []
    if not dirs:
        return "Error: agents directory not configured"
    if not ctx.llm_client:
        return "Error: LLM client not available"
    if not ctx.tool_registry:
        return "Error: tool registry not available"
    if ctx.agent_depth >= ctx.max_agent_depth:
        return f"Error: max agent nesting depth ({ctx.max_agent_depth}) reached"

    # Strip .md if provided
    if name.endswith(".md"):
        name = name[:-3]

    # Search dirs in reverse order so local overrides builtin
    path = None
    for d in reversed(dirs):
        candidate = Path(d) / f"{name}.md"
        if candidate.exists():
            path = candidate
            break

    if path is None:
        available = scan_agents(dirs)
        avail_list = ", ".join(available.keys()) if available else "none"
        return f"Unknown agent '{name}'. Available: {avail_list}"

    try:
        content = path.read_text(encoding="utf-8")
        meta, body = _parse_agent(content)
    except Exception as e:
        return f"Error loading agent '{name}': {e}"

    # Substitute args into prompt template
    # If template has {task} but args has description/question — remap
    try:
        prompt = body.format_map(agent_args)
    except KeyError:
        # Try common aliases: task/description/question are interchangeable
        remapped = dict(agent_args)
        first_val = next(iter(agent_args.values()), "") if agent_args else ""
        for key in ("task", "description", "question", "target", "path", "symbol", "error"):
            if key not in remapped:
                remapped[key] = first_val
        try:
            prompt = body.format_map(remapped)
        except KeyError as e:
            return f"Error: missing argument {e} for agent '{name}'"

    prompt += _execution_contract()

    allowed_tools = meta.get("tools", ["read", "glob", "grep"])
    allowed_tools = list(allowed_tools)
    # Every sub-agent can consult and contribute to the same session memory.
    if ctx.memory_store is not None and "memory" not in allowed_tools:
        allowed_tools.append("memory")
    max_steps = meta.get("max_steps", 5)

    # Classify agent: light (read-only) vs full
    WRITE_TOOLS = {"edit", "bash", "agent", "hex_write"}
    agent_operation = "agent_light" if not (set(allowed_tools) & WRITE_TOOLS) else "agent"
    if name == "planner":
        agent_operation = "planner"

    # Nested agents only if explicitly listed in frontmatter tools
    next_depth = ctx.agent_depth + 1

    # Special handling for planner — collect plan_steps
    is_planner = name == "planner"
    if is_planner:
        from emelya.tools.plan_step import reset as plan_reset, get_steps
        plan_reset()

    depth_str = f" (depth {next_depth})" if next_depth > 1 else ""
    try:
        from emelya.theme import C as _C
        print(f"\n  {_C.tool_call}[agent: {name}{depth_str}]{_C.reset} {_C.muted}Running...{_C.reset}", flush=True)
    except ImportError:
        print(f"\n  [agent: {name}{depth_str}] Running...", flush=True)

    text_result = _run_agent_loop(
        prompt=prompt,
        allowed_tools=allowed_tools,
        max_steps=max_steps,
        ctx=ctx,
        depth=next_depth,
        agent_name=name,
        operation=agent_operation,
        user_language=(
            ctx.user_language or _detect_task_language(agent_args)
        ),
    )

    # If planner — return plan as JSON instead of text
    if is_planner:
        steps = get_steps()
        if steps:
            return json.dumps({"plan": steps, "summary": text_result}, ensure_ascii=False)
    return text_result


def _run_agent_loop(prompt: str, allowed_tools: list, max_steps: int,
                    ctx, depth: int = 1, agent_name: str = "",
                    operation: str = "agent", user_language: str = "") -> str:
    """Run isolated agent loop with progress output and routing support."""
    import sys
    import time
    from emelya.tools import ToolContext

    try:
        from emelya.theme import C as _C
    except ImportError:
        class _C:
            tool_call = muted = tool_result = tool_error = reset = ""

    indent = "  " * depth

    user_language = user_language or _detect_task_language(prompt)
    messages = [
        {"role": "system", "content": _language_system_message(user_language)},
        {"role": "user", "content": prompt},
    ]
    tools = _language_bound_tools(
        ctx.tool_registry.get_tools(allowed_tools), user_language
    )

    # Resolve LLM client via routing
    llm_client = ctx.llm_client
    routed_model = ""
    if ctx.get_routed_client:
        try:
            llm_client, routed_model = ctx.get_routed_client(operation)
        except Exception:
            pass

    sub_ctx = ToolContext(
        work_dir=ctx.work_dir,
        sandbox=ctx.sandbox,
        files_read=set(),
        confirm=ctx.confirm,
        get_cancellation_result=ctx.get_cancellation_result,
        config=ctx.config,
        agent_depth=depth,
        max_agent_depth=ctx.max_agent_depth,
        agents_dirs=ctx.agents_dirs,
        llm_client=llm_client,
        tool_registry=ctx.tool_registry,
        session_log=ctx.session_log,
        usage_tracker=ctx.usage_tracker,
        get_routed_client=ctx.get_routed_client,
        memory_store=ctx.memory_store,
        task_store=ctx.task_store,
        user_language=user_language,
    )

    if routed_model and routed_model != "local":
        try:
            print(f"{indent}  {_C.muted}via {routed_model}{_C.reset}", flush=True)
        except Exception:
            pass

    action_log = []  # track what agent did for error reporting
    _seen_calls = {}  # duplicate detection
    external_calls = 0
    external_failures = 0

    for step in range(max_steps):
        t0 = time.monotonic()
        sys.stdout.write(f"\r\033[K{indent}{_C.muted}  [{agent_name}] step {step + 1}/{max_steps}...{_C.reset}")
        sys.stdout.flush()

        # Reserve two attempts for synthesis. The penultimate attempt normally
        # produces the result; the last one can repair an empty/progress-only
        # response without spending another tool call.
        remaining = max_steps - step - 1
        reserved_summaries = 2 if max_steps >= 3 else 1
        summary_phase = remaining < reserved_summaries
        step_tools = None if summary_phase else (tools if tools else None)
        if remaining == reserved_summaries - 1:
            messages.append({
                "role": "user",
                "content": (
                    "Stop investigation now. Produce the requested deliverable from "
                    "the evidence already collected. Do not request or describe another "
                    "tool call. Use the same language as the user's task."
                ),
            })

        try:
            response = llm_client.chat(
                messages=messages,
                tools=step_tools,
                temperature=ctx.config.temperature if ctx.config else 0.7,
                max_tokens=ctx.config.max_tokens if ctx.config else 1024,
            )
            _usage = response.get("usage")
            _model = response.get("model_id") or (llm_client.model if llm_client else None)
            _log = ctx.session_log
            if _log:
                _log.response(
                    response.get("content"), response.get("tool_calls"),
                    usage=_usage, model=_model,
                )
            if _usage and ctx.usage_tracker:
                ctx.usage_tracker.record(
                    model=_model or "unknown",
                    prompt=_usage.get("prompt_tokens", 0),
                    completion=_usage.get("completion_tokens", 0),
                    total=_usage.get("total_tokens", 0),
                )
        except Exception as e:
            sys.stdout.write(f"\r\033[K")
            sys.stdout.flush()
            return _format_agent_error(agent_name, step, max_steps, action_log,
                                       f"LLM error: {e}")

        elapsed = time.monotonic() - t0
        sys.stdout.write(f"\r\033[K")
        sys.stdout.flush()

        assistant_msg = response["raw"]
        messages.append(assistant_msg)

        tool_calls = response.get("tool_calls")
        if tool_calls and step_tools is None:
            for tc in tool_calls:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": (
                        "Error: investigation budget is exhausted; the tool was not "
                        "executed. Return the final deliverable now."
                    ),
                })
            if remaining > 0:
                continue
            return _format_agent_error(
                agent_name, step, max_steps, action_log,
                "agent requested tools after the investigation budget was exhausted",
            )
        if not tool_calls:
            content = response.get("content") or ""
            invalid_reason = _invalid_final_result(content)
            if invalid_reason and remaining > 0:
                messages.append({
                    "role": "user",
                    "content": (
                        f"The previous response is not a deliverable ({invalid_reason}). "
                        "Return the final result now, using the user's language."
                    ),
                })
                continue
            if invalid_reason:
                return _format_agent_error(
                    agent_name, step, max_steps, action_log,
                    invalid_reason,
                )
            return content

        for tc in tool_calls:
            tc_name = tc["name"]
            tc_args = tc["args"]

            # Display
            if tc_name == "bash":
                desc = tc_args.get("description", "")
                cmd = tc_args.get("command", "")
                print(f"{indent}  {_C.tool_call}bash:{_C.reset} {desc}")
                if cmd:
                    print(f"{indent}  {_C.muted}$ {cmd}{_C.reset}")
            elif tc_name == "read":
                print(f"{indent}  {_C.tool_call}read{_C.reset} {_C.muted}{tc_args.get('path', '')}{_C.reset}")
            elif tc_name == "grep":
                print(f"{indent}  {_C.tool_call}grep{_C.reset} {_C.muted}{tc_args.get('pattern', '')}{_C.reset}")
            elif tc_name == "glob":
                print(f"{indent}  {_C.tool_call}glob{_C.reset} {_C.muted}{tc_args.get('pattern', '')}{_C.reset}")
            elif tc_name == "agent":
                print(f"{indent}  {_C.tool_call}agent{_C.reset} {_C.muted}{tc_args.get('name', '')}{_C.reset}")
            else:
                detail = str(tc_args)[:80] if tc_args else ""
                print(f"{indent}  {_C.tool_call}{tc_name}{_C.reset} {_C.muted}{detail}{_C.reset}")

            # Duplicate detection
            _call_key = (tc_name, json.dumps(tc_args, sort_keys=True))
            _seen_calls[_call_key] = _seen_calls.get(_call_key, 0) + 1
            if _seen_calls[_call_key] > 1:
                result = (
                    f"Error: duplicate call to {tc_name} with same arguments. "
                    "Try a different approach."
                )
                action_log.append(f"{tc_name}(DUPLICATE)")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                continue

            # Execute and track
            result = ctx.tool_registry.execute(tc_name, tc_args, sub_ctx)
            if tc_name in {"web_search", "web_fetch"}:
                external_calls += 1
                normalized = (result or "").strip().lower()
                if normalized.startswith("error:") or normalized in {"", "(empty response)"}:
                    external_failures += 1

            # Build action log entry
            if tc_name == "read":
                lines_match = __import__("re").search(r"of (\d+)\]", result)
                n_lines = lines_match.group(1) if lines_match else "?"
                action_log.append(f"read {tc_args.get('path', '?')} ({n_lines} lines)")
            elif tc_name == "grep":
                n_matches = result.count("\n") + 1 if "No matches" not in result else 0
                action_log.append(f"grep '{tc_args.get('pattern', '')}' ({n_matches} matches)")
            elif tc_name == "glob":
                n_files = result.count("\n") + 1 if "No matches" not in result else 0
                action_log.append(f"glob '{tc_args.get('pattern', '')}' ({n_files} files)")
            elif tc_name == "bash":
                action_log.append(f"bash: {tc_args.get('description', tc_args.get('command', ''))[:60]}")
            else:
                action_log.append(f"{tc_name}({str(tc_args)[:40]})")

            if result.startswith("Error:"):
                action_log[-1] += f" FAILED: {result[:80]}"
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": _compact_tool_result(result),
            })

        if external_failures >= 3 or external_calls >= 6:
            action_log.append("external circuit breaker: synthesizing available evidence")
            break

    # Step limit — force summary
    sys.stdout.write(f"\r\033[K{indent}{_C.muted}  [{agent_name}] summarizing...{_C.reset}")
    sys.stdout.flush()
    messages.append({
        "role": "user",
        "content": "Step limit reached. Provide your answer based on what you have found so far.",
    })
    try:
        response = llm_client.chat(
            messages=messages,
            tools=None,
            temperature=ctx.config.temperature if ctx.config else 0.7,
            max_tokens=ctx.config.max_tokens if ctx.config else 1024,
        )
        _usage = response.get("usage")
        _model = response.get("model_id") or (llm_client.model if llm_client else None)
        if ctx.session_log:
            ctx.session_log.response(
                response.get("content"), None,
                usage=_usage, model=_model,
            )
        if _usage and ctx.usage_tracker:
            ctx.usage_tracker.record(
                model=_model or "unknown",
                prompt=_usage.get("prompt_tokens", 0),
                completion=_usage.get("completion_tokens", 0),
                total=_usage.get("total_tokens", 0),
            )
        sys.stdout.write(f"\r\033[K")
        sys.stdout.flush()
        content = response.get("content") or ""
        if _looks_like_unexecuted_tool_call(content):
            return _format_agent_error(
                agent_name, max_steps, max_steps, action_log,
                "model emitted a tool call as plain text during final summary",
            )
        if not content.strip():
            return _format_agent_error(
                agent_name, max_steps, max_steps, action_log,
                "agent returned no final result",
            )
        return content
    except Exception as e:
        sys.stdout.write(f"\r\033[K")
        sys.stdout.flush()
        return _format_agent_error(agent_name, max_steps, max_steps, action_log,
                                   f"LLM error during summary: {e}")


def _format_agent_error(agent_name, step, max_steps, action_log, error_msg):
    """Format informative error with action history."""
    parts = [f"Error: agent '{agent_name}' failed at step {step + 1}/{max_steps}."]
    if action_log:
        parts.append(f"Completed actions: {'; '.join(action_log)}.")
    parts.append(f"Error: {error_msg}")
    return " ".join(parts)


def _looks_like_unexecuted_tool_call(content: str) -> bool:
    """Detect provider-specific tool markup returned as ordinary assistant text."""
    text = content.lower()
    return (
        "<tool_call>" in text
        or "<function=" in text
        or ("<function_calls>" in text and "<invoke" in text)
    )


def _looks_like_progress_only(content: str) -> bool:
    """Reject short promises of future work presented as a completed result."""
    text = " ".join(content.strip().lower().split())
    if len(text) >= 240:
        return False
    prefixes = (
        "let me ", "i'll ", "i will ", "now let me ", "next i ",
        "давайте ", "сейчас я ", "теперь я ", "проверю ", "посмотрю ",
    )
    return text.startswith(prefixes)


def _invalid_final_result(content: str) -> str:
    """Return a model-facing reason when text is not a usable deliverable."""
    if not content.strip():
        return "agent returned no result"
    if _looks_like_unexecuted_tool_call(content):
        return "model emitted a tool call as plain text; it was not executed"
    if _looks_like_progress_only(content):
        return "response only announces future work"
    return ""


def _execution_contract() -> str:
    """Universal contract shared by all specialized agents."""
    return (
        "\n\nExecution contract:\n"
        "- Keep the investigation bounded to the requested deliverable.\n"
        "- Use the same language as the user's task for the final response and all "
        "user-visible tool descriptions. Technical identifiers may remain unchanged.\n"
        "- Finish with an actual result, not a promise to continue or a textual tool call.\n"
        "- State whether the result is complete, partial, or blocked; include concrete "
        "evidence and name any remaining work.\n"
        "- Reserve enough interaction budget to synthesize the result.\n"
    )


def _detect_task_language(value) -> str:
    """Detect Russian from nested task arguments; default to English."""
    if isinstance(value, dict):
        text = " ".join(str(item) for item in value.values())
    elif isinstance(value, (list, tuple)):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value or "")
    return "ru" if any("\u0400" <= char <= "\u04ff" for char in text) else "en"


def _language_system_message(language: str) -> str:
    """Return a high-priority language contract for an isolated sub-agent."""
    if language == "ru":
        return (
            "Язык пользователя — русский. Пиши по-русски все видимые пользователю "
            "сообщения: итог, промежуточные пояснения, названия шагов, а также поля "
            "description/title/summary в вызовах инструментов. Команды, код, пути, "
            "имена файлов и идентификаторы не переводи. Это системное требование."
        )
    return (
        "The user's language is English. Write all user-visible messages and tool "
        "description/title/summary fields in English. Keep commands, code, paths, "
        "file names, and identifiers unchanged. This is a system requirement."
    )


def _language_bound_tools(tools: list, language: str) -> list:
    """Place the language requirement next to model-authored display fields."""
    bound = copy.deepcopy(tools)
    instruction = (
        "Значение должно быть написано по-русски."
        if language == "ru"
        else "Write this value in English."
    )
    for tool in bound:
        properties = (
            tool.get("function", {}).get("parameters", {}).get("properties", {})
        )
        for field_name in ("description", "title", "summary"):
            field = properties.get(field_name)
            if isinstance(field, dict):
                existing = field.get("description", "")
                field["description"] = (existing + " " + instruction).strip()
    return bound




def _compact_tool_result(result: str, limit: int = 8000) -> str:
    """Bound sub-agent history growth while preserving both ends of output."""
    if len(result) <= limit:
        return result
    head = limit * 3 // 4
    tail = limit - head
    omitted = len(result) - limit
    return (
        result[:head]
        + f"\n\n[... {omitted} characters omitted from agent context; "
          "use a narrower query or paged read ...]\n\n"
        + result[-tail:]
    )
