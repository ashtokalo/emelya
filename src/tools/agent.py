"""Agent tool — run sub-agents as isolated inference cycles."""

import json
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


def scan_agents(agents_dir: Path) -> dict:
    """Scan agents directory, return {name: description}."""
    agents = {}
    if not agents_dir.is_dir():
        return agents

    for f in sorted(agents_dir.glob("*.md")):
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

    if not ctx.agents_dir:
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

    path = ctx.agents_dir / f"{name}.md"
    if not path.exists():
        available = scan_agents(ctx.agents_dir)
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

    allowed_tools = meta.get("tools", ["read", "glob", "grep"])
    max_steps = meta.get("max_steps", 5)

    # Classify agent: light (read-only) vs full
    WRITE_TOOLS = {"write", "edit", "bash", "agent", "hex_write"}
    agent_operation = "agent_light" if not (set(allowed_tools) & WRITE_TOOLS) else "agent"
    if name == "planner":
        agent_operation = "planner"

    # Nested agents only if explicitly listed in frontmatter tools
    next_depth = ctx.agent_depth + 1

    # Special handling for planner — collect plan_steps
    is_planner = name == "planner"
    if is_planner:
        from tools.plan_step import reset as plan_reset, get_steps
        plan_reset()

    depth_str = f" (depth {next_depth})" if next_depth > 1 else ""
    try:
        from theme import C as _C
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
    )

    # If planner — return plan as JSON instead of text
    if is_planner:
        steps = get_steps()
        if steps:
            return json.dumps({"plan": steps, "summary": text_result}, ensure_ascii=False)
    return text_result


def _run_agent_loop(prompt: str, allowed_tools: list, max_steps: int,
                    ctx, depth: int = 1, agent_name: str = "",
                    operation: str = "agent") -> str:
    """Run isolated agent loop with progress output and routing support."""
    import sys
    import time
    from tools import ToolContext

    try:
        from theme import C as _C
    except ImportError:
        class _C:
            tool_call = muted = tool_result = tool_error = reset = ""

    indent = "  " * depth

    messages = [{"role": "user", "content": prompt}]
    tools = ctx.tool_registry.get_tools(allowed_tools)

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
        config=ctx.config,
        agent_depth=depth,
        max_agent_depth=ctx.max_agent_depth,
        agents_dir=ctx.agents_dir,
        llm_client=llm_client,
        tool_registry=ctx.tool_registry,
        session_log=ctx.session_log,
        usage_tracker=ctx.usage_tracker,
        get_routed_client=ctx.get_routed_client,
    )

    if routed_model and routed_model != "local":
        try:
            print(f"{indent}  {_C.muted}via {routed_model}{_C.reset}", flush=True)
        except Exception:
            pass

    action_log = []  # track what agent did for error reporting

    for step in range(max_steps):
        t0 = time.monotonic()
        sys.stdout.write(f"\r\033[K{indent}{_C.muted}  [{agent_name}] step {step + 1}/{max_steps}...{_C.reset}")
        sys.stdout.flush()

        try:
            response = llm_client.chat(
                messages=messages,
                tools=tools if tools else None,
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
        if not tool_calls:
            return response.get("content") or "Agent returned no result"

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

            # Execute and track
            result = ctx.tool_registry.execute(tc_name, tc_args, sub_ctx)

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
                "content": result,
            })

    # Step limit — force summary
    sys.stdout.write(f"\r\033[K{indent}{_C.muted}  [{agent_name}] summarizing...{_C.reset}")
    sys.stdout.flush()
    messages.append({
        "role": "user",
        "content": "Step limit reached. Provide your answer based on what you have found so far.",
    })
    try:
        response = ctx.llm_client.chat(
            messages=messages,
            tools=None,
            temperature=ctx.config.temperature if ctx.config else 0.7,
            max_tokens=ctx.config.max_tokens if ctx.config else 1024,
        )
        _usage = response.get("usage")
        _model = response.get("model_id") or (llm_client.model if llm_client else None)
        _log = ctx.session_log
        if _log:
            _log.response(
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
        return response.get("content") or "Agent returned no result"
    except Exception as e:
        sys.stdout.write(f"\r\033[K")
        sys.stdout.flush()
        return _format_agent_error(agent_name, max_steps, max_steps, action_log,
                                   f"LLM error during summary: {e}")


def _format_agent_error(agent_name, step, max_steps, action_log, error_msg):
    """Format informative error with action history."""
    parts = [f"Agent '{agent_name}' failed at step {step + 1}/{max_steps}."]
    if action_log:
        parts.append(f"Completed actions: {'; '.join(action_log)}.")
    parts.append(f"Error: {error_msg}")
    return " ".join(parts)
