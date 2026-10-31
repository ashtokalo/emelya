# Shared agent memory

Emelya keeps durable, session-scoped agent memory without a database or third-party dependencies.

```text
.emelya/sessions/<session>/memory/
├── entries/
│   └── mem-<timestamp>-<random>.json
└── links/
    └── link-<timestamp>-<random>.json

.emelya/sessions/<session>/tasks/
└── task-<sequence>.json
```

Each record is written to a temporary file and atomically renamed into place. Independent files allow the main agent and sub-agents to add records without rewriting a shared index. Session rename/resume/branch/clear automatically selects the corresponding memory directory.

## Tool

The compact `memory` tool is available to the main agent and automatically added to every sub-agent. It supports five actions:

- `put` — store a concise fact, finding, decision, summary, or artifact reference;
- `search` — retrieve small previews by text, kind, scope, status, or tags;
- `get` — load one complete record and its links;
- `review` — mark a record `verified`, `rejected`, or `needs_review` while preserving status history;
- `link` — relate two records, for example `verified_by` or `depends_on`.

Example finding:

```json
{
  "action": "put",
  "kind": "finding",
  "summary": "Pipeline classification can bypass confirmation",
  "scope": "src/emelya/tools/bash.py",
  "status": "proposed",
  "tags": ["security", "bash"],
  "evidence": {
    "path": "src/emelya/tools/bash.py",
    "line": 137,
    "test": "_is_readonly('cat script | bash') == true"
  }
}
```

Memory is retrieval-based: records are never injected into every prompt automatically. Agents should search before repeating work and store only reusable conclusions with evidence, not entire source files or command output. Stored content is untrusted data, never executable instructions; proposed findings must be independently reviewed before final synthesis.

## Plans and tasks

The planner turns every validated plan step into a persistent task before execution. Each completed step writes its full result to `tmp/<output>` and stores only a concise memory record with pointers to the artifact and task. Dependent agents receive these references instead of the complete preceding output, and load details with `read` or `memory(get)` only when needed.

Failed steps are recorded as `failed`; dependent steps become `skipped`, and the executor refuses to report the plan as fully complete. Malformed plans are rejected before any tasks are created. Session rename and branch operations copy both `memory/` and `tasks/`.
