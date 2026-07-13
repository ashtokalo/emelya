"""REPL /agent command."""

from emelya.i18n import t


def _cmd_agent(self, arg: str = ""):
    if not arg:
        from emelya.tools.agent import scan_agents
        agents = scan_agents(self.agents_dirs)
        if agents:
            print(f"  {t('available')}:")
            for name, desc in agents.items():
                print(f"    {name} — {desc}")
        else:
            print("  No agents available")
        return

    parts = arg.split(None, 1)
    name = parts[0]
    task = parts[1] if len(parts) > 1 else ""

    if not task:
        print(f"  Usage: /agent {name} <task description>")
        return

    self._agent_loop(f"Use agent '{name}' for this task: {task}")
