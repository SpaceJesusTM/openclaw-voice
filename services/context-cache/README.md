# Hot-context and recent-result cache

Implemented for Priority 1 as a module of the single ClawVoice runtime, not a
separate service: `services/voice/clawvoice/context/` (generic cache and
built-in resolvers) and `services/voice/clawvoice/plugins/` (domain resolvers).

Per-agent topics, resolvers and freshness policy are configured in
`services/voice/agents/<id>.yaml`. See [docs/voice.md](../../docs/voice.md#hot-context)
for semantics (fresh/stale/expired/refreshing/unavailable, refresh cadence,
read-your-writes invalidation, day rollover) and how to add a topic. Credentials
and private message contents are never cached.
