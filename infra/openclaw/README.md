# Minimal OpenClaw foundation for ClawVoice

A separate one-agent OpenClaw 2026.9.9 installation adapted
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
`http://127.0.0.1:18939` by default.

There is no Google integration: no Calendar, no Gmail, and no `gog` in the image.
The day planner is local-only.
Tari starts with Sol and no automatic model fallbacks. OpenClaw manages models,
utility selection and credentials natively; ClawVoice imposes no model/profile allowlist.
See the [audit results](../../docs/openclaw-audit.md). To keep future personalization flexible, voice identity,
agent display name, and multi-agent routing belong to the future ClawVoice
application layer.

The persistent [Tari day planner](../../docs/planner.md) adds local blocks, commutes
and a categorized checklist. Existing installations can run `manage.py install-planner`
to add it without resetting private state; use `manage.py planner show tomorrow`.
