"""Local environment loading for operator-invoked commands."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_local_environment(path: str | Path | None = None) -> bool:
    """Load an ignored .env file without replacing explicitly exported values."""

    candidate = Path(path) if path is not None else Path.cwd() / ".env"
    return load_dotenv(dotenv_path=candidate, override=False, verbose=False)
