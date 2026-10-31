# plan_step

Add a step to the execution plan. Used inside the planner agent.

## Parameters

- `description` (string, required) — What this step does.
- `agent` (string, optional) — Agent to run (e.g. "explore_dir", "review_file").
- `tool` (string, optional) — Tool to run directly (e.g. "read", "grep"). Use agent OR tool, not both.
- `args` (object, optional) — Arguments for the agent/tool.
- `output` (string, optional) — Filename to save result (e.g. "structure.md"). Stored in session tmp dir.
- `uses` (array of strings, optional) — Output files from previous steps this step needs.

## Examples

plan_step(description="Explore project structure", agent="explore_dir", args={"path": "."}, output="structure.md")
plan_step(description="Review main module", agent="review_file", args={"path": "src/repl.py"}, output="review_repl.md")
plan_step(description="Verify findings", agent="verify_findings", args={"task": "Verify proposed defects"}, output="verification.md", uses=["review_repl.md"])
plan_step(description="Create final report", agent="report_compiler", args={"task": "Compile audit report"}, output="report.md", uses=["structure.md", "review_repl.md", "verification.md"])

## Notes

- Steps are collected, not executed immediately. The REPL executes them after the planner finishes.
- The last step typically compiles previous outputs into the final result.
- Each step runs in isolation — it doesn't see other steps' contexts.
- Dependencies are passed as artifact paths and memory IDs, not copied in full.
