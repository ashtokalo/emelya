"""Configuration — CLI args + defaults."""

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    host: str = "localhost"
    port: int = 8080
    model: str = "default"
    model_name: str = None  # name from models.json, None = "local"
    temperature: float = 0.7
    max_tokens: int = 8192
    context_limit: int = 8000
    work_dir: Path = None
    max_iterations: int = 10
    stream: bool = True
    confirm_mode: str = "confirm_all"  # confirm_all | confirm_write | no_confirm
    resume_session: str = None  # session name to resume

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def config_file(self) -> Path:
        return self.work_dir / "emelya.md"


def parse_args(argv=None) -> Config:
    parser = argparse.ArgumentParser(
        description="Emelya — CLI AI Agent for local LLMs"
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model", default="default",
                        help="Model name on the server (default: default)")
    parser.add_argument("--use", default=None, metavar="NAME",
                        help="Use a named model from .emelya/models.json")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--context-limit", type=int, default=8000)
    parser.add_argument("--work-dir", default=".")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--resume", default=None, metavar="SESSION",
                        help="Resume a previous session by name")
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument(
        "--confirm",
        choices=["all", "write", "none"],
        default="all",
        help="Bash confirmation mode (default: all)",
    )

    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir).resolve()
    confirm_map = {"all": "confirm_all", "write": "confirm_write", "none": "no_confirm"}

    # Load persistent settings (.emelya/settings.json) — CLI args override
    settings = _load_settings(work_dir)
    cli_explicit = _get_explicit_args(parser, argv)

    def pick(cli_val, cli_name, settings_key, default):
        """CLI arg wins if explicitly passed, else settings file, else default."""
        if cli_name in cli_explicit:
            return cli_val
        return settings.get(settings_key, default)

    return Config(
        host=pick(args.host, "host", "host", "localhost"),
        port=pick(args.port, "port", "port", 8080),
        model=pick(args.model, "model", "model", "default"),
        model_name=pick(args.use, "use", "use", None),
        temperature=pick(args.temperature, "temperature", "temperature", 0.7),
        max_tokens=pick(args.max_tokens, "max_tokens", "max_tokens", 8192),
        context_limit=pick(args.context_limit, "context_limit", "context_limit", 8000),
        work_dir=work_dir,
        max_iterations=pick(args.max_iterations, "max_iterations", "max_iterations", 10),
        stream=not pick(args.no_stream, "no_stream", "no_stream", False),
        confirm_mode=confirm_map[pick(args.confirm, "confirm", "confirm", "all")],
        resume_session=args.resume,
    )


def _load_settings(work_dir: Path) -> dict:
    """Load .emelya/settings.json if it exists."""
    import json
    settings_path = work_dir / ".emelya" / "settings.json"
    if settings_path.exists():
        try:
            return json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _get_explicit_args(parser, argv) -> set:
    """Determine which CLI args were explicitly passed (not defaults)."""
    # Parse with defaults removed to see what user actually typed
    sentinel = object()
    defaults = {}
    for action in parser._actions:
        if action.dest == "help":
            continue
        defaults[action.dest] = action.default
        action.default = sentinel

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return set()
    finally:
        # Restore defaults
        for action in parser._actions:
            if action.dest in defaults:
                action.default = defaults[action.dest]

    return {k for k, v in vars(args).items() if v is not sentinel}
