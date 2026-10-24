# OpenClaw foundation: owner setup

Start here from the repository root. This independent demo pins OpenClaw
**2026.9.9**, with one agent, stable ID `tari`. There is no Google integration:
no Calendar, no Gmail and no `gog`. The agent's tools are the local day planner
and checklist.

**Current checkpoint:** Docker build, startup, schema, health, authentication
boundary and isolated storage passed on macOS/arm64. The audit-created demo is
running at `127.0.0.1:18939`. ChatGPT OAuth, real Sol inference and two-turn
continuity pass on this checkout; the local planner is also installed. (Google
reads and Calendar management were validated earlier and have since been removed.)
Fresh installations continue at step 2. See the [audit evidence](openclaw-audit.md).

## 1. Fresh installation

Prerequisites: Docker Desktop/Engine with Compose v2, Git, Python 3.10+ with IANA
timezone data, a normal non-root Docker user, and a ChatGPT account entitled to
the intended Codex model. Windows users should run the manager inside WSL.

```bash
python3 infra/openclaw/manage.py init --timezone America/Toronto --name Tari
python3 infra/openclaw/manage.py start
python3 infra/openclaw/manage.py doctor
```

`init` creates only missing files under ignored `infra/openclaw/.local/`.
It generates an independent Gateway secret and a random Compose project
name. Directories use mode 700, private files 600, and workspace helpers 700.
The container uses your host UID/GID for writable bind mounts. Linux users need
Docker access without running this manager as root.
`start` waits for container health and fails if it cannot become healthy within
90 seconds; `logs` and `status` provide startup diagnostics.

The host port is loopback-only **18939**, separate from Tari's 18899; the internal
container port is 18789. For a fresh installation choose another free port with
`init --port 19501` if needed. Repeating init preserves credentials, config and
workspace content; it does **not** apply later template changes. For an older
installation, review and merge template changes into private config/workspace
before starting. OpenClaw validates its own configuration schema; the manager
does not impose model or authentication policy. Customize personality in the
private `IDENTITY.md` and `SOUL.md` after initialization.

No production Tari directory or host Codex home is mounted
or imported. The foundation runs independently of the voice experiments.

## 2. Sign in to ChatGPT/Codex

Run in **your own interactive terminal**:

```bash
python3 infra/openclaw/manage.py openclaw models auth login --provider openai --device-code --agent tari
```

Follow the displayed sign-in instructions yourself. Never paste device codes,
callback URLs, passwords, tokens or client JSON into chat, an issue or Git.
Device sign-in avoids publishing a Docker OAuth callback port. If your account
requires enabling device-code login, enable it in your ChatGPT/Codex security
settings. If disallowed, replace `--device-code` with `--method oauth` and follow
the browser/manual callback prompts locally.

The pinned CLI's flags and provider implementation were inspected. The subscription
provider is **`openai`**, so the intended route remains **`openai/gpt-6.1-sol`**.
This demo explicitly selects the embedded OpenClaw runtime, which uses its internal
ChatGPT/Codex transport with the selected OAuth profile. It does not depend on
automatic native Codex app-server installation.

Tari initially uses **GPT-6.1 Sol** with automatic model fallbacks disabled.
Models, utility-model selection, session overrides, authentication profiles and
per-agent tools belong to OpenClaw's native configuration. ClawVoice does not
require a particular utility model, model allowlist or OAuth profile name.
Use the forwarded `openclaw models ...` and `openclaw config ...` commands to
manage them. `CLAWVOICE_MODEL` is an initialization default, not the active
configuration's source of truth. Existing installations keep their working login.

Do not add `--set-default` during this demo's sign-in if you want to keep Sol as
Tari's default: onboarding can recommend another model. This demo uses the existing
ChatGPT OAuth credentials and has no API-key backup configured. Future agents may
use explicitly configured credentials; host model credentials are never
inherited automatically into the demo container.

OAuth credentials persist in this installation's `/home/node/.openclaw` state
mount. In this release credential/session storage includes SQLite databases under
`state/agents/`; do not assume an older `auth-profiles.json` location.

Check auth, then exercise two independent conversations and continuity:

```bash
python3 infra/openclaw/manage.py openclaw models status --agent tari --json --check
python3 infra/openclaw/manage.py chat "Our fictional verification phrase is copper otter. Reply with that phrase." --conversation sol-a
python3 infra/openclaw/manage.py chat "What verification phrase did I give you?" --conversation sol-a
python3 infra/openclaw/manage.py chat "Our fictional verification phrase is violet kite. Reply with that phrase." --conversation sol-b
python3 infra/openclaw/manage.py chat "What verification phrase did I give you?" --conversation sol-b
python3 infra/openclaw/manage.py openclaw sessions --agent tari --json
```

Verify each session recalls its own phrase; inspect its provider/model metadata
for `openai` / `gpt-6.1-sol`. An assistant's self-description is not model evidence.
The HTTP request's `openclaw/tari` is an **agent selector**, not the provider model.
Conversation IDs select session continuity; they never grant authentication.
The manager reads the server-side Gateway token privately.

For an explicit live provider probe after sign-in:

```bash
python3 infra/openclaw/manage.py openclaw models status --agent tari --probe --probe-provider openai --json
```

This uses your subscription allowance. Before OAuth, Sol is configured but
unavailable and `--check` fails: expected, not a passing model test. A catalog
entry does not prove account entitlement. If your account rejects Sol, make an
explicit account/model decision before changing the configuration; no substitute is selected automatically.

After chats succeed, test persistence:

```bash
python3 infra/openclaw/manage.py stop
python3 infra/openclaw/manage.py start
python3 infra/openclaw/manage.py chat "Repeat this conversation's verification phrase." --conversation sol-a
```

## 3. Verify agent invocation

```bash
python3 infra/openclaw/manage.py chat "Use ./bin/day-planner to show tomorrow's planner as JSON, then summarize it. State the timezone." --conversation planner-check
```

Confirm tool execution/results in the private session trajectory; a plausible
summary is insufficient evidence.

## 4. Diagnostics and stopping

```bash
python3 -m unittest discover -s infra/openclaw/tests -v
python3 infra/openclaw/manage.py doctor
python3 infra/openclaw/manage.py status
python3 infra/openclaw/manage.py logs
python3 infra/openclaw/manage.py openclaw --help
python3 infra/openclaw/manage.py stop
```

`doctor` validates Compose and the actual OpenClaw schema,
checks HTTP health, rejection without a token, and acceptance
of an authenticated malformed request without model inference. It does not test
OAuth entitlement, inference or semantic memory search.

The container has a read-only root filesystem, private state mounts,
temporary caches, dropped capabilities and no-new-privileges. Cron, heartbeats,
autonomous skill work and memory dreaming are disabled; browser and delegation/
Gateway control tools are disabled. Workspace/memory/session fundamentals remain
available. The helper is executable by the agent; successful agent use awaits
live acceptance.

The agent has shell access in this disposable container. The wrapper and prompt
are guardrails, not a sandbox against a malicious shell process.
Mounting only demo data keeps this separate from production Tari. The Gateway token
is an owner credential; keep it server-side. Loopback publishing does not protect
it from other containers sharing the Docker network.

Logs are warning-level. Private transcripts, SQLite stores and secrets stay under
ignored `.local`. Treat diagnostics as private; sanitize before sharing.
Do not publish account emails, response bodies, callback URLs, tokens or recovery
information.

Common failures:

- **Docker unavailable/permission denied:** start Docker Desktop or fix your normal
  user's Docker access. Do not run init as root or copy production state.
- **Missing model auth:** finish step 2 and inspect `models status --agent tari --json --check`.
- **Sol rejected after OAuth:** stop for an explicit account/model decision.
- **Port occupied:** leave the other service alone. Choose a different fresh
  installation port, or deliberately update private env and allowed origins.
- **Updates absent after pulling:** init preserves private files. Compare tracked
  templates and merge relevant changes locally.
- **Gateway warns about a non-loopback bind:** `lan` is inside Docker for port
  forwarding; the verified host mapping is `127.0.0.1` only.

`stop` retains demo state. For permanent disposal, stop first, then remove only
this checkout's generated Compose resources and `.local` after reviewing/backing
up desired state. This deletes demo credentials and memory. Never target production
Tari resources. Revoke demo grants in account settings when no longer needed.

References:
[OpenClaw OpenAI setup](https://docs.openclaw.ai/providers/openai/setup),
[runtime routing](https://docs.openclaw.ai/concepts/agent-runtimes),
[Codex authentication](https://learn.chatgpt.com/docs/auth).
Pinned runtime inspection takes precedence over examples for newer releases.

## Local planner and checklist

Follow [planner setup, commands and timezone evidence](planner.md). Fresh init
includes it; an existing workspace uses `python3 infra/openclaw/manage.py install-planner`
for a targeted addition. No credentials or sessions are reset.
