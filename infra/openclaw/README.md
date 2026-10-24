# Minimal OpenClaw foundation for ClawVoice

A separate one-agent OpenClaw 2026.9.9 + gogcli 0.40.0 installation adapted
from Tari Public. The agent is named Tari by default, with stable ID `tari`.
No production Tari data, OAuth credentials, Pipecat or Boson secrets are copied.

See [the complete setup and verification checklist](../../docs/openclaw-setup.md).

```bash
python3 infra/openclaw/manage.py init --timezone America/Toronto
python3 infra/openclaw/manage.py start
python3 infra/openclaw/manage.py doctor
python3 infra/openclaw/manage.py openclaw models auth login --provider openai --device-code --agent tari
```

The `openai/gpt-6.1-sol` model route is configured, **not authenticated**
until you complete ChatGPT/Codex OAuth locally. The Gateway listens at
`http://127.0.0.1:18939` by default. Google requires a separate,
manually approved OAuth profile; the complete procedure is in the setup guide.

Gmail remains **read-only**. Calendar supports full native event management
(create/edit/delete, recurrence, attendees and invitation responses) through
`calendar manage` after separate Calendar OAuth authorization.
Tari starts with Sol and no automatic model fallbacks. OpenClaw manages models,
utility selection and credentials natively; ClawVoice imposes no model/profile allowlist.
See the [audit results](../../docs/openclaw-audit.md). To keep future personalization flexible, voice identity,
agent display name, and multi-agent routing belong to the future ClawVoice
application layer.

The persistent [Tari day planner](../../docs/planner.md) adds local blocks, commutes
and a categorized checklist. Existing installations can run `manage.py install-planner`
to add it without resetting private state; use `manage.py planner show tomorrow`.

## Isolated Astra Coding Agent (`--agent astra`)

Astra (`id: astra`) runs in its own isolated OpenClaw container (`compose.astra.yaml`)
with private state under `infra/openclaw/.local-astra/` and an isolated coding workspace
mounted at `<repo>/astra-workspace` (`/home/node/.openclaw/workspace`). Astra has no access
to Tari's planner, Google credentials, or the ClawVoice source repository.

- **Gateway loopback port**: `http://127.0.0.1:18949` (`ws://127.0.0.1:18949`)
- **Local preview loopback port**: `http://127.0.0.1:18950`

```bash
python3 infra/openclaw/manage.py --agent astra init --timezone America/Toronto
python3 infra/openclaw/manage.py --agent astra start
python3 infra/openclaw/manage.py --agent astra doctor
python3 infra/openclaw/manage.py --agent astra openclaw models auth login --provider openai --device-code --agent astra
```
