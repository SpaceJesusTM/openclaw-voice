"""ClawVoice local server.

    cd services/voice && make dev        # http://127.0.0.1:7860

Pipecat's development runner provides the WebRTC signalling (/start,
/sessions/{id}/api/offer) and the stock playground at /client/. ClawVoice adds:

    GET  /                      the ClawVoice web UI (apps/web)
    GET  /api/agents            configured agent identities and Team mode availability (browser-safe)
    GET  /api/state?agent=ID    gateway health, cache topics, jobs (no secrets)
    POST /api/jobs/{id}/cancel  explicit job cancellation
    POST /api/agents/{id}/gateway/reconnect   re-check the Gateway (e.g. after pairing)
    POST /api/agents/{id}/cache/{topic}/refresh

The process-level runtime (agents, caches, jobs) starts with the server and
outlives individual voice connections. Bind to loopback only.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pipecat.runner.run import app, main

from clawvoice.agents import load_registry
from clawvoice.bot import bot  # noqa: F401 - the Pipecat runner discovers bot() in __main__
from clawvoice.config import MODEL, load_settings
from clawvoice.paths import WEB_ROOT, state_dir
from clawvoice.runtime import Runtime, get_runtime, set_runtime
from clawvoice.team.bot import team_members
from clawvoice.voice.telemetry import configure_logging

_LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}


@asynccontextmanager
async def lifespan(_app):
    settings = load_settings()
    configure_logging(settings.api_key, level="INFO")
    runtime = Runtime(load_registry(), state_directory=state_dir())
    set_runtime(runtime)
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()
        set_runtime(None)


app.router.lifespan_context = lifespan


@app.middleware("http")
async def same_origin_guard(request: Request, call_next):
    """Refuse state-changing requests from other web origins (local CSRF guard)."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            host = request.headers.get("host", "")
            if parsed.hostname not in _LOOPBACK or parsed.netloc != host:
                return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
    return await call_next(request)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_ROOT / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/ui", StaticFiles(directory=str(WEB_ROOT)), name="clawvoice-ui")


@app.get("/api/agents")
async def agents():
    runtime = get_runtime()
    members = team_members(runtime)
    return {
        "default": runtime.registry.default_id,
        "model": MODEL,
        "agents": [agent.agent.public_identity() for agent in runtime.agents.values()],
        # Team mode: every agent in one room, addressed by name (POST /start body {"mode": "team"}).
        "team": {"available": len(members) >= 2, "agents": [rt.agent.id for rt in members]},
    }


def _agent(agent_id: str | None):
    try:
        return get_runtime().agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown agent") from None


@app.get("/api/state")
async def state(agent: str | None = None):
    return {"model": MODEL, **_agent(agent).state()}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, agent: str | None = None):
    return await _agent(agent).jobs.cancel(job_id.upper(), source="ui")


@app.post("/api/agents/{agent_id}/gateway/reconnect")
async def reconnect(agent_id: str):
    runtime = _agent(agent_id)
    mode = await runtime.backend.select_transport(force=True)
    return {"active_transport": mode, "gateway": runtime.backend.health()}


@app.post("/api/agents/{agent_id}/cache/{topic}/refresh")
async def refresh(agent_id: str, topic: str):
    runtime = _agent(agent_id)
    if topic not in runtime.cache.topic_keys:
        raise HTTPException(status_code=404, detail="Unknown topic")
    ok = await runtime.cache.refresh_now(topic, "owner request")
    return {"refreshed": ok, "topic": next(r for r in runtime.cache.status() if r["topic"] == topic)}


def run() -> None:
    # Validate configuration before the runner starts listening.
    settings = load_settings()
    configure_logging(settings.api_key)
    load_registry()
    args = sys.argv[1:]
    if "-t" not in args and "--transport" not in args:
        sys.argv += ["-t", "webrtc"]
    if "--host" not in args:
        sys.argv += ["--host", "127.0.0.1"]
    main()


if __name__ == "__main__":
    run()
