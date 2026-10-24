# Shared contracts

Priority 1 contracts are defined in Python inside the single ClawVoice runtime
(one consumer, so no separate package yet):

| Contract | Definition |
| --- | --- |
| Agent profile (identity, Gateway, helpers, topics, delegation) | `services/voice/clawvoice/agents.py`, examples in `services/voice/agents/*.yaml` |
| Hot-context snapshot and reading | `services/voice/clawvoice/context/models.py`, `context/cache.py` |
| Background job, expectations, verification | `services/voice/clawvoice/jobs/models.py` |
| Agent run outcome and receipt | `services/voice/clawvoice/openclaw/outcomes.py`, `openclaw/receipts.py` |

Extract language-neutral schemas here when a second consumer (for example a
richer frontend or another runtime) needs them.
