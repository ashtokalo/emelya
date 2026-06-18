# agent

Run a sub-agent in an isolated context.

## Parameters

- `name` (string, required) — Name of the agent to run.
- `args` (object, optional) — Arguments to pass to the agent.

## Examples

agent(name="researcher", args={"query": "find all TODO comments"})
agent(name="fixer", args={"file": "src/main.py", "issue": "type error on line 42"})

## Notes

- The sub-agent runs in an isolated context with its own conversation.
- Each agent definition specifies which tools are available to it.
- Maximum nesting depth is 3.
