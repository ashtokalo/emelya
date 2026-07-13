"""REPL /skill command."""

from emelya.i18n import t


def _cmd_skill(self, arg: str = ""):
    if not arg:
        from emelya.tools.skill import scan_skills
        skills = scan_skills(self.skills_dirs)
        if skills:
            print(f"  {t('available')}:")
            for name, desc in skills.items():
                print(f"    {name} — {desc}")
        else:
            print("  No skills available")
        return

    result = self.registry.execute("skill", {"name": arg}, self.tool_ctx)
    if result.startswith("Error:") or result.startswith("Unknown"):
        print(f"  {result}")
        return

    self.context.add_user(f"/skill {arg}")
    self.context.add_assistant_text(f"Skill '{arg}' loaded.")
    self._agent_loop(f"I loaded skill '{arg}'. Follow its instructions. Here it is:\n{result}")
