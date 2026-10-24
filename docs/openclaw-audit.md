# OpenClaw foundation audit — 2026-10-23

**Current status after configuration simplification:** real GPT-6.1 Sol inference
and two-turn conversation continuity pass through the authenticated local Gateway.
Native session metadata records provider `openai` and model `gpt-6.1-sol`; native
`models status --agent tari --json --check` passes. Google authorization and reads
remain pending. Continue with [Google owner setup](openclaw-setup.md#3-register-and-authorize-a-different-google-account).

The manager's custom `validate_policy()` and model/auth environment enforcement
were removed. Tari keeps Sol as its initial default with no automatic fallbacks.
Utility selection, session model overrides, additional agents and authentication
profiles use OpenClaw's native configuration. The template no longer forces a
utility model, model allowlist or OAuth profile ID. Existing OAuth credentials
and profile selection were preserved.

The actual blocked config had Sol primary/utility already configured, but its
model allowlist also contained `openai/*`. The custom validator rejected that
broader native configuration. No custom migration machinery was added. The active
installation was adjusted with OpenClaw's own `config unset` for only
`agents.defaults.utilityModel` and `agents.entries.tari.modelPolicy`, with an
ignored private backup. Before restarting/chatting, byte checks confirmed that
the env, Google store, session and workspace files were untouched; all other
configuration, including OAuth and Gateway auth, was identical.

Validation after simplification: **25 offline tests pass**, including legacy config
chat compatibility, state/permission isolation and Google read-only protections.
Native configuration schema and model/auth readiness pass; Docker startup/health
and the authenticated HTTP boundary pass. A fresh two-turn Gateway conversation
successfully recalls a fictional phrase. No Google reads or production Tari
operations were performed. The disposable demo remains running.

## Original audit snapshot

The following records the earlier foundation audit. Its strict model/profile
checks and pending-inference status are superseded by the update above.

## Scope and findings

Audited `feat/openclaw-google-foundation` starting at `1cf60dd`, with Tari Public
commit `c43455a43f59a554fd6e9e96934741ac4397a27d` as a read-only reference.
The existing application/voice/runtime/orchestrator/cache/contracts split is
appropriate; future directories remain reserved. Both completed experiments
were preserved. No production Tari installation, credentials or Google state
was accessed, copied or mounted.

| Finding | Repair / conclusion |
| --- | --- |
| Copied Google helper retained calendar proposal/apply/create/update/delete machinery | Removed the entire write surface; no environment variable can enable it |
| Google account/profile could inherit alternate selections and gog environment overrides | Require explicit demo account, fixed `tari-read` profile; remove inherited gog options except isolated storage/keyring settings |
| Gmail read sanitization removed links but did not protect recognizable account-security messages | Add conservative security-content/code filtering; preserve IDs/pagination; document over-redaction and limits |
| Google result validation was incomplete | Reject malformed mail/event shapes and incomplete pagination; retain source identity, fail closed without claiming an empty schedule |
| Docs offered an optional API-key billing fallback and speculative login flags | Remove fallback; verify the pinned device-code CLI, select dedicated OAuth profile and document the exact command |
| Sol primary had no fallback, but runtime supplied implicit utility model `openai/gpt-5.6-luna` | Explicitly pin utility calls to Sol and restrict model overrides to Sol |
| Default runtime selection could choose native Codex and install/repair its runtime | Explicitly use the embedded OpenClaw runtime with its subscription OAuth transport |
| File creation applied private modes after opening; intermediate directories could retain default modes | Create files with private permissions immediately; repair existing template/env modes and set each managed directory to 700 |
| Nested `--help` was consumed by the manager | Forward OpenClaw/gog/helper help to the actual installed CLI |
| Startup returned before the Gateway was healthy | Wait for Compose health with a 90-second timeout |
| `doctor` reported only Compose status | Validate installed policy, Compose, runtime schema, gog version, health and the authenticated HTTP boundary |
| Attribution named MIT but omitted its full notice | Preserve Tari Public's complete MIT copyright/permission text |

Compared with Tari Public, this foundation retains only core OpenClaw, identity/
workspace/memory/session capabilities, the Gateway and Gmail/Calendar read skills.
No DailyGrad, planner, ingestion extensions, Discord or workflow plugins were
added. Google writes, cron, heartbeats, autonomous skill work and memory dreaming
are disabled. Browser and delegation/Gateway control tools are disabled.

The public model ref **`openai/gpt-6.1-sol` is correct for this pinned release**:
inspection of the actual image's provider contract includes `gpt-6.1-sol` and
normalizes selected OpenAI OAuth inference to `openai-chatgpt-responses`.
The dedicated profile is `openai:clawvoice`, OAuth only. This is route support,
not evidence that the owner's subscription currently grants the model.

## Verification performed

| Category | Evidence / result |
| --- | --- |
| Offline foundation | `python3 -m unittest discover -s infra/openclaw/tests -v`: **25 passed** |
| Python/config syntax | `py_compile` for manager/helper and JSON parsing: passed |
| Compose | Generated installation + `docker compose … config --quiet`: passed |
| Image build | Actual `docker build` and `manage.py start` build: passed on macOS/arm64 |
| OpenClaw binary | Actual `--version`: **2026.9.9 (bcfc888)** |
| gog binary | Actual `--version`: **v0.40.0 (a3065f53)** |
| gog architecture pins | Both amd64/arm64 SHA-256 values match upstream v0.40.0 release checksums; arm64 archive verified during build |
| Runtime schema | Actual `config validate --json`: valid, no warnings |
| Startup/health | Actual gateway starts and Docker health becomes healthy at `127.0.0.1:18939` |
| HTTP authentication | Missing/wrong bearer token: 401, including a session ID; correct token with invalid body: 400 before inference |
| Container isolation | Inspected only this demo container: read-only root, all capabilities dropped, no-new-privileges, host UID/GID, loopback mapping, only two bind mounts under this checkout's `.local` |
| Persistent writes | Container can write/delete disposable markers in isolated state and Google store; agent helper executable |
| Stop/start | Health recovers; generated credentials, config and disposable workspace marker persist |
| Model config | Actual `models status --agent tari --json` resolves Sol primary + Sol utility, empty fallbacks; only Sol allowed |
| No account yet | Actual status has no OAuth profiles and missing `openai`; configured Sol row reports unavailable, as expected |
| Direct Higgs regression | `npm test`: **24 passed**; `npm run test:browser`: **7 passed**; `npm run build`: passed |
| Pipecat regression | `make check`: **18 passed**, Ruff and compilation passed; two existing dependency deprecation warnings |

Offline tests cover initialization/idempotence/permissions/path guards, OAuth
identity metadata compatibility, credential inheritance isolation, model/billing/
write policy, safe stopping with invalid policy, nested CLI help, removal of
mutations, fixed Google selection/flags, upstream errors/timeouts/invalid JSON,
security-content filtering, pagination and incomplete/empty schedules.

The existing Dockerfile/Compose startup and base configuration were compatible;
no version upgrade or Compose rewrite was needed. Both release checksums were
verified, but an amd64/Linux runtime build was **not** exercised here.
Gateway warning logs contain the expected internal-container `lan` bind notice;
the host mapping was independently checked as loopback-only.

## Account-dependent acceptance — pending

No real model/provider inference or Google API read was performed during this
audit. No sign-in flow was started or authorization code requested in chat.

1. Owner ChatGPT/Codex sign-in; verify exact OAuth profile and Sol entitlement.
2. Two independent conversations with continuity, provider/model metadata and
   a post-restart turn. Do not rely on model self-identification.
3. Owner Google Cloud Desktop client, exact separate account and readonly grants.
4. Gmail search/read and Calendar discovery/list/get through the helper.
5. The same operations initiated by the actual OpenClaw agent; inspect tool evidence.
6. OAuth refresh/persistence with real credentials and live memory/session behavior.

Semantic/vector memory retrieval was not tested. The foundation retains workspace
and core memory fundamentals without claiming a validated embeddings provider.

## Isolation limits

Private config, env, credentials, memory and sessions stay under ignored `.local`,
which is excluded from the image build context. No secrets were added to Git.
The manager does not import host Codex/Google/Tari credentials. Gateway credentials
remain server-side; a conversation ID is not authentication.

The agent has shell access to its disposable container. Wrapper restrictions and
prompt instructions are guardrails; read-only OAuth scopes are the Google-side
write boundary. Confirm actual scopes after consent. The mail sanitizer is
conservative, can over-redact and cannot guarantee recognition of arbitrary
embedded secrets. Keep diagnostics/private data out of shared demos.

The unauthenticated demo is left running for owner setup. No production resource
was changed. Stop only this demo with `python3 infra/openclaw/manage.py stop`.
