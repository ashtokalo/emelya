# task

Track progress of multi-step work. Tasks are shown to the user as a status line.

## Parameters

- `action` (string, required) — "create", "update", "complete", "fail", or "list"
- `title` (string) — Task description (for create/update)
- `id` (int) — Task ID (for update/complete)

## Examples

task(action="create", title="Reading source files")
task(action="create", title="Analyzing architecture")
task(action="complete", id=1)
task(action="update", id=2, title="Analyzing architecture (3 files)")
task(action="complete", id=2)
task(action="list")

## Notes

- Use for complex multi-step work to show progress.
- Create tasks at the start, complete them as you finish.
- Tasks are persisted in the current session and survive resume.
- User sees: [1/3] Reading source files
