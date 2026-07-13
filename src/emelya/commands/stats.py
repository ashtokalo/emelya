"""REPL /stats command."""

from emelya.i18n import t
from emelya.log import UsageTracker, collect_stats


def _print_stats(self):
    stats = collect_stats(self.sessions_dir)

    def fmt(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)

    def print_section(title, by_model, by_date, totals):
        if totals["requests"] == 0:
            print(f"\n  {title}: no data")
            return
        print(f"\n  === {title} ===\n")
        if by_model:
            print(f"  {t('by_model')}:")
            for model, m in sorted(by_model.items()):
                print(f"    {model}: {fmt(m['total'])} tokens"
                      f" ({fmt(m['prompt'])} in + {fmt(m['completion'])} out)"
                      f" / {m['requests']} req")
        if by_date:
            print(f"\n  {t('by_date')}:")
            for date, d in sorted(by_date.items()):
                print(f"    {date}: {fmt(d['total'])} tokens / {d['requests']} req")
        print(f"\n  {t('total')}: {fmt(totals['total'])} tokens / {totals['requests']} req")

    cur = stats.get("by_session", {}).get(self.session_name)
    if cur:
        print_section(
            f"{t('session')}: {self.session_name}",
            cur.get("by_model", {}),
            cur.get("by_date", {}),
            cur,
        )

    usage_path = self.config.work_dir / ".emelya" / "usage.jsonl"
    project_stats = UsageTracker.load_stats(usage_path)
    print_section(
        t("project_total"),
        project_stats.get("by_model", {}),
        project_stats.get("by_date", {}),
        project_stats.get("totals", {"requests": 0}),
    )
