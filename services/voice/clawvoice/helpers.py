"""Run trusted, deterministic OpenClaw-side read helpers configured per agent.

Helpers are argv templates from agent configuration (``helpers:`` in
``agents/<id>.yaml``), for example an agent's own planner or Calendar read
commands executed inside its OpenClaw container. They read authoritative state
without a model run. This module adds no business logic: it runs the
configured command, bounds it, and parses its JSON.

Safety: no shell; a minimal environment that never includes Boson or Gateway
credentials; output size and time limits; stderr is kept out of logs because
it can contain private schedule text. Identical concurrent calls share one
process (single-flight), and a short memo absorbs refresh bursts.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

from clawvoice.paths import REPO_ROOT, SERVICE_ROOT

MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_PASSTHROUGH_ENV = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "DOCKER_HOST",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
    "CLAWVOICE_FIXTURE_DIR",  # test fixtures only; never a credential
)


class HelperError(RuntimeError):
    """A helper failed. Messages are generic and safe to show the owner."""

    def __init__(self, message: str, *, kind: str = "failed", exit_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.exit_code = exit_code


@dataclass(frozen=True)
class HelperResult:
    data: Any
    exit_code: int
    duration_ms: int
    argv_label: str


def expand(template: tuple[str, ...] | list[str], args: list[str]) -> list[str]:
    values = {"python": sys.executable, "repo": str(REPO_ROOT), "service": str(SERVICE_ROOT)}
    return [part.format(**values) for part in template] + [str(arg) for arg in args]


def extract_json(text: str) -> Any:
    """Parse the JSON document a helper printed, tolerating leading noise lines."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty output")
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    for index, line in enumerate(stripped.splitlines()):
        if line.lstrip().startswith(("{", "[")):
            return json.loads("\n".join(stripped.splitlines()[index:]))
    raise ValueError("no JSON document in helper output")


class HelperRunner:
    def __init__(
        self,
        helpers: dict[str, tuple[str, ...]],
        *,
        max_concurrency: int = 2,
        default_timeout: float = 60.0,
        memo_seconds: float = 3.0,
        extra_env: dict[str, str] | None = None,
    ):
        self.helpers = dict(helpers)
        self.default_timeout = default_timeout
        self.memo_seconds = memo_seconds
        self.extra_env = dict(extra_env or {})
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._inflight: dict[tuple[str, ...], asyncio.Future] = {}
        self._memo: dict[tuple[str, ...], tuple[float, HelperResult]] = {}
        self.calls = 0
        self.shared = 0

    def has(self, helper: str) -> bool:
        return helper in self.helpers

    def _env(self) -> dict[str, str]:
        env = {key: os.environ[key] for key in _PASSTHROUGH_ENV if key in os.environ}
        env["PYTHONPATH"] = str(SERVICE_ROOT)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(self.extra_env)
        return env

    async def run_json(
        self, helper: str, args: list[str], *, timeout: float | None = None, fresh: bool = False
    ) -> HelperResult:
        if helper not in self.helpers:
            raise HelperError(f"Helper {helper!r} is not configured for this agent", kind="config")
        argv = tuple(expand(self.helpers[helper], args))
        now = time.monotonic()
        if not fresh:
            memo = self._memo.get(argv)
            if memo and memo[0] > now:
                self.shared += 1
                return memo[1]
            pending = self._inflight.get(argv)
            if pending is not None:
                self.shared += 1
                return await asyncio.shield(pending)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        if not fresh:
            self._inflight[argv] = future
        try:
            result = await self._execute(helper, argv, timeout or self.default_timeout)
            if self.memo_seconds > 0:
                self._memo[argv] = (time.monotonic() + self.memo_seconds, result)
            future.set_result(result)
            return result
        except BaseException as error:
            if not future.done():
                future.set_exception(error)
                future.exception()  # mark retrieved for shielded waiters
            raise
        finally:
            if self._inflight.get(argv) is future:
                del self._inflight[argv]

    async def _execute(self, helper: str, argv: tuple[str, ...], timeout: float) -> HelperResult:
        label = f"{helper} {' '.join(argv[len(self.helpers[helper]):])}".strip()
        async with self._semaphore:
            self.calls += 1
            started = time.monotonic()
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._env(),
                    cwd=str(REPO_ROOT),
                )
            except OSError as error:
                raise HelperError(f"Helper {helper!r} could not start ({type(error).__name__})", kind="start") from None
            try:
                stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise HelperError(f"Helper {helper!r} timed out after {timeout:g}s", kind="timeout") from None
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise
            duration_ms = round((time.monotonic() - started) * 1000)
            if len(stdout) > MAX_OUTPUT_BYTES:
                raise HelperError(f"Helper {helper!r} output exceeded the size limit", kind="output")
            if process.returncode != 0:
                raise HelperError(
                    f"Helper {helper!r} exited with status {process.returncode}",
                    kind="exit",
                    exit_code=process.returncode,
                )
            try:
                data = extract_json(stdout.decode("utf-8", errors="replace"))
            except ValueError:
                raise HelperError(f"Helper {helper!r} returned no valid JSON", kind="output") from None
            return HelperResult(data=data, exit_code=0, duration_ms=duration_ms, argv_label=label)
