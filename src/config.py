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
    max_tokens: int = 1024
    context_limit: int = 8000
    work_dir: Path = None
    max_iterations: int = 10
    stream: bool = True
    confirm_mode: str = "confirm_all"  # confirm_all | confirm_write | no_confirm

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def config_file(self) -> Path:
        return self.work_dir / "aigent.md"


def parse_args(argv=None) -> Config:
    parser = argparse.ArgumentParser(
        description="Aigent — CLI AI Agent for local LLMs"
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model", default="default",
                        help="Model name on the server (default: default)")
    parser.add_argument("--use", default=None, metavar="NAME",
                        help="Use a named model from .aigent/models.json")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--context-limit", type=int, default=8000)
    parser.add_argument("--work-dir", default=".")
    parser.add_argument("--max-iterations", type=int, default=20)
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

    return Config(
        host=args.host,
        port=args.port,
        model=args.model,
        model_name=args.use,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        context_limit=args.context_limit,
        work_dir=work_dir,
        max_iterations=args.max_iterations,
        stream=not args.no_stream,
        confirm_mode=confirm_map[args.confirm],
    )
