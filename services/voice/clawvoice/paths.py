"""Filesystem locations. Private runtime state lives in the ignored .local/ tree."""

from __future__ import annotations

import os
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVICE_ROOT.parent.parent
AGENTS_DIR = SERVICE_ROOT / "agents"
WEB_ROOT = REPO_ROOT / "apps" / "web"


def state_dir() -> Path:
    """Private, git-ignored directory for device identity, jobs and diagnostics."""
    override = os.environ.get("CLAWVOICE_STATE_DIR")
    path = Path(override).expanduser().resolve() if override else SERVICE_ROOT / ".local"
    private_dir(path)
    return path


def captures_dir() -> Path:
    path = SERVICE_ROOT / "captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def write_private(path: Path, text: str) -> None:
    """Atomically replace a private (0600) file."""
    private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
