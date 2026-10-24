# ClawVoice OpenClaw + Gmail/Calendar setup

This is a **new, independent** demo installation based on minimal reusable parts
of [Tari Public](https://github.com/SpaceJesusTM/tari-public). It pins
OpenClaw `2026.9.9` and `gogcli 0.40.0`. It has one configurable demo
agent named Tari (`tari` is its stable machine ID). Planner, DailyGrad,
Discord, scheduled automation, and voice-to-OpenClaw are intentionally absent.

## Prerequisites

Use your own Mac/PC with Docker Desktop/Engine + Compose v2, Git,
Python 3.10+, a ChatGPT/Codex account with model access, and optionally
a Google Cloud OAuth client for a **different** Google account than production Tari.

Run from the repository root:

```bash
python3 infra/openclaw/manage.py init --timezone America/Toronto --name Tari
python3 infra/openclaw/manage.py start
python3 infra/openclaw/manage.py status
```

`init` is non-destructive. It creates **only missing** files under ignored
`infra/openclaw/.local/`: an isolated Compose project identity, private Gateway
token, keyring password, OpenClaw config, agent workspace and empty Google
credential storage. It will not read or copy your production `~/.openclaw`,
`~/.config/gogcli`, or any private Tari project.

The Gateway listens on **127.0.0.1:18939** by default, not Tari's 18899.
The container's own Gateway port is 18789. If 18939 is occupied, choose an
unused port in the initial `init --port` call (before any initialization).

The local manager accepts `status`, `logs`, `doctor`, `stop`,
`openclaw ...`, `gog ...`, `google ...`, and `chat "message"`.

## A. Authenticate your ChatGPT account for GPT-6.1 Sol

Do **not** put your ChatGPT password, tokens, or an OAuth callback URL in a chat,
commit, or issue. Use OpenClaw's own model authentication inside **this**
container:

```bash
python3 infra/openclaw/manage.py openclaw models auth login --provider openai --agent tari
```

OpenClaw's supported Codex subscription authentication uses a browser OAuth or
device-code approval flow. If the normal browser callback cannot complete from
Docker, inspect the installed CLI:

```bash
python3 infra/openclaw/manage.py openclaw models auth login --help
```

Then use its supported device-code option (newer versions accept
`--method device-code` or `--device-code`), and complete the approval
yourself. **Do not assume a flag exists in the pinned release without checking.**

Check the resulting agent model route and credential readiness:

```bash
python3 infra/openclaw/manage.py openclaw models list --provider openai
python3 infra/openclaw/manage.py openclaw models status --agent tari --json --check
python3 infra/openclaw/manage.py chat "Reply with exactly: ClawVoice Sol connected." --conversation sol-check
python3 infra/openclaw/manage.py chat "What was the verification phrase?" --conversation sol-check
```

The desired foundation model is **`openai/gpt-6.1-sol`**. A configured route
or `models status` is not proof of live inference: the two chat messages
are the actual runtime acceptance check. ChatGPT OAuth uses your eligible
Codex subscription allowance; actual access to Sol depends on your account.
Never silently substitute another model or incur separate OpenAI API billing.

Optional `OPENAI_API_KEY` is blank in `.local/runtime.env`; you do not need an
API key if your ChatGPT/Codex OAuth profile works.

## B. Connect a separate Google account (Gmail + Calendar only)

**Google uses OAuth, not an API key.** You may reuse an existing Google Cloud
OAuth *client configuration* if its consent screen, test users and granted
scopes permit this separate account, but do **not** copy Tari's existing
Google access/refresh tokens, keyring, or authorized profile. A new desktop
OAuth client is cleaner for the hackathon.

In [Google Cloud Console](https://console.cloud.google.com/):
1. Choose a project and enable Gmail API and Google Calendar API.
2. Configure an OAuth consent screen (External for personal accounts, add
   the demo Google account under Test users if in Testing).
3. Create a **Desktop app OAuth client**, download its JSON, and copy it
   *locally* to `infra/openclaw/.local/google/client_secret.json`.
4. Restrict that file's permissions (`chmod 600`) and do not commit it.

The intended OAuth scopes are
`https://www.googleapis.com/auth/gmail.readonly` and
`https://www.googleapis.com/auth/calendar.readonly`.
Neither Calendar nor Gmail writes are enabled in this foundation.

Edit the ignored file `infra/openclaw/.local/runtime.env` and set
`TARI_GOOGLE_ACCOUNT=your-other-account@example.com`.
Keep `TARI_CALENDAR_WRITES=0`, then restart this demo container:

```bash
python3 infra/openclaw/manage.py stop
python3 infra/openclaw/manage.py start
```

Register the downloaded OAuth client and authorize only the demo account
in this demo installation:

```bash
python3 infra/openclaw/manage.py gog --client tari-read auth credentials set /home/node/.config/gogcli/client_secret.json
python3 infra/openclaw/manage.py gog --client tari-read auth add your-other-account@example.com --services gmail,calendar --gmail-scope readonly --readonly --manual
python3 infra/openclaw/manage.py gog --client tari-read auth list --check
```

The manual flow prints an authorization URL. Open it on your machine and
paste its *complete* redirect URL into your **own terminal** when prompted.
A redirect page failing to load may be expected in the manual flow.
Do not paste the URL, authorization code, or client JSON into this chat.
A testing-mode consent screen may issue short-lived refresh credentials;
Google or Workspace admin policies may block access.

Verify the helper (not just the token listing):

```bash
python3 infra/openclaw/manage.py google calendar calendars
python3 infra/openclaw/manage.py google calendar list --start '2026-10-24T00:00:00-04:00' --end '2026-10-25T00:00:00-04:00' --json
python3 infra/openclaw/manage.py google gmail search 'newer_than:1d' --max 5
```

Dates are illustrative; adjust to the desired local day. Account data is
printed in your terminal; do not publish transcripts or diagnostics.
Then ask the actual agent to summarize your demo calendar and search mail.
**A passing helper test is not proof the agent can use it**; both checks matter.

Google Drive, Docs and Sheets are not in this first milestone; Tari Public's
reusable Google functionality here is Gmail and Calendar.

## C. Diagnostics and security

```bash
python3 infra/openclaw/manage.py doctor
python3 infra/openclaw/manage.py logs
python3 infra/openclaw/manage.py openclaw --help
python3 infra/openclaw/manage.py stop
```

The Gateway is deliberately loopback-only. Its token is an operator credential:
do not expose it to browsers, Boson, Discord, other users, or public tunnels.
A future Pipecat bridge must run server-side with allowlisted agent IDs and
operation approvals. OAuth files, memory, chats, runtime.env, and Google keyring
stay in ignored `infra/openclaw/.local/`.

Do not delete or modify existing production Tari containers, volumes, auth
profiles, or Google configuration. The private Compose project identifier
prevents name collisions. Avoid copying real Gmail bodies into project demos.
If the setup fails, report the exact failing command, sanitized error and
model/account selection, **never the token**.

## Verification status

The repository scaffold and the configuration template are committed. **No live
OAuth approval, ChatGPT model turn, container build, or Google read has been
performed by ChatGPT in this branch.** These require local Docker, your account
approval and private keys. Run the acceptance commands above; only after
passing them should we say the OpenClaw/Google integration works.
