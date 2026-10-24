"""Server-side secret references used by agent configuration.

Configuration names *where* a secret lives, never the secret itself:

- ``env:NAME``                  a process environment variable
- ``envfile:path/to/file#NAME`` a KEY=value file under the repository root

Values are resolved only in the server process. They are never returned to
the browser, written to traces, or included in error messages.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values

from clawvoice.paths import REPO_ROOT

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class SecretError(RuntimeError):
    """A secret reference could not be resolved. Never carries the value."""


def describe(ref: str) -> str:
    """A loggable form of a reference. References name a location, never a value."""
    return str(ref)


def resolve(ref: str, *, root: Path = REPO_ROOT, environ: dict[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    if not isinstance(ref, str) or ":" not in ref:
        raise SecretError("Secret references must use env:NAME or envfile:PATH#NAME")
    kind, _, rest = ref.partition(":")
    if kind == "env":
        if not _NAME.fullmatch(rest):
            raise SecretError(f"Invalid environment variable name in {describe(ref)}")
        value = environ.get(rest, "")
    elif kind == "envfile":
        path_text, sep, name = rest.partition("#")
        if not sep or not _NAME.fullmatch(name):
            raise SecretError(f"Invalid envfile reference {describe(ref)}; use envfile:PATH#NAME")
        path = Path(path_text)
        path = (root / path) if not path.is_absolute() else path
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise SecretError(f"Secret file for {describe(ref)} must be inside the repository checkout")
        if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(root)):
            raise SecretError(f"Refusing symlinked secret file for {describe(ref)}")
        if not resolved.is_file():
            raise SecretError(f"Secret file for {describe(ref)} is missing")
        value = str(dotenv_values(resolved).get(name) or "")
    else:
        raise SecretError(f"Unsupported secret reference kind {kind!r}")
    value = value.strip()
    if not value:
        raise SecretError(f"Secret {describe(ref)} is empty or not set")
    return value


def resolve_optional(ref: str | None, **kwargs) -> str | None:
    if not ref:
        return None
    try:
        return resolve(ref, **kwargs)
    except SecretError:
        return None
