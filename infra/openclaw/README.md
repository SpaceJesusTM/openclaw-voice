# Minimal OpenClaw foundation for ClawVoice

A separate one-agent OpenClaw 2026.9.9 + gogcli 0.40.0 installation adapted
from Tari Public. The agent is named Tari by default, with stable ID `tari`.
No production Tari data, OAuth credentials, Pipecat or Boson secrets are copied.

See [the complete setup and verification checklist](../../docs/openclaw-setup.md).

```bash
python3 infra/openclaw/manage.py init --timezone America/Toronto
python3 infra/openclaw/manage.py start
python3 infra/openclaw/manage.py openclaw models auth login --provider openai --agent tari
python3 infra/openclaw/manage.py chat "Hello, what model are you using?"
```

The `openai/gpt-6.1-sol` model route is configured, **not authenticated**
until you complete ChatGPT/Codex OAuth locally. The Gateway listens at
`http://127.0.0.1:18939` by default. Google requires a separate,
manually approved OAuth profile; the complete procedure is in the setup guide.

Only Gmail and Calendar **reads** are enabled. Do not enable writes during
foundation testing. To keep future personalization flexible, voice identity,
agent display name, and multi-agent routing belong to the future ClawVoice
application layer.
