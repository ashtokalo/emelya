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
    try:
        prompt = body.format_map(agent_args)
    except KeyError as e:
        return f"Error: missing argument {e} for agent '{name}'"

    allowed_tools = meta.get("tools", ["read", "glob", "grep"])
    max_steps = meta.get("max_steps", 5)

    # Nested agents only if explicitly listed in frontmatter tools
    next_depth = ctx.agent_depth + 1

    depth_str = f" (depth {next_depth})" if next_depth > 1 else ""
    try:
        from theme import C as _C
        print(f"\n  {_C.tool_call}[agent: {name}{depth_str}]{_C.reset} {_C.muted}Running...{_C.reset}", flush=True)
    except ImportError:
        print(f"\n  [agent: {name}{depth_str}] Running...", flush=True)

    return _run_agent_loop(
        prompt=prompt,
        allowed_tools=allowed_tools,
        max_steps=max_steps,
        ctx=ctx,
        depth=next_depth,
        agent_name=name,
    )


def _run_agent_loop(prompt: str, allowed_tools: list, max_steps: int,
                    ctx, depth: int = 1, agent_name: str = "") -> str:
    """Run isolated agent loop with progress output. Returns final text."""
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

    sub_ctx = ToolContext(
        work_dir=ctx.work_dir,
        sandbox=ctx.sandbox,
        files_read=set(),
        confirm=ctx.confirm,
        config=ctx.config,
        agent_depth=depth,
        max_agent_depth=ctx.max_agent_depth,
        agents_dir=ctx.agents_dir,
        llm_client=ctx.llm_client,
        tool_registry=ctx.tool_registry,
    )

    for step in range(max_steps):
        t0 = time.monotonic()
        # Show waiting indicator
        sys.stdout.write(f"\r\033[K{indent}{_C.muted}  [{agent_name}] step {step + 1}/{max_steps}...{_C.reset}")
        sys.stdout.flush()

        try:
            response = ctx.llm_client.chat(
                messages=messages,
                tools=tools if tools else None,
                temperature=ctx.config.temperature if ctx.config else 0.7,
                max_tokens=ctx.config.max_tokens if ctx.config else 1024,
            )
            # Log usage for stats
            _log = ctx.session_log
            if _log:
                _log.response(
                    response.get("content"), response.get("tool_calls"),
                    usage=response.get("usage"),
                    model=response.get("model_id") or (ctx.llm_client.model if ctx.llm_client else None),
                )
        except Exception as e:
            sys.stdout.write(f"\r\033[K")
            sys.stdout.flush()
            return f"Agent error: {e}"

        elapsed = time.monotonic() - t0
        sys.stdout.write(f"\r\033[K")
        sys.stdout.flush()

        assistant_msg = response["raw"]
        messages.append(assistant_msg)

        tool_calls = response.get("tool_calls")
        if not tool_calls:
            return response.get("content") or "Agent returned no result"

        # Show tool calls
        for tc in tool_calls:
            tc_name = tc["name"]
            tc_args = tc["args"]
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

            result = ctx.tool_registry.execute(tc_name, tc_args, sub_ctx)
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
        _log = ctx.session_log
        if _log:
            _log.response(
                response.get("content"), None,
                usage=response.get("usage"),
                model=response.get("model_id") or (ctx.llm_client.model if ctx.llm_client else None),
            )
        sys.stdout.write(f"\r\033[K")
        sys.stdout.flush()
        return response.get("content") or "Agent returned no result"
    except Exception as e:
        sys.stdout.write(f"\r\033[K")
        sys.stdout.flush()
        return f"Agent error: {e}"
