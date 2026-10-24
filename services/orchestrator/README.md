# Orchestrator

Priority 1 implements background-job coordination and the single-agent speaking
floor inside the ClawVoice runtime: `services/voice/clawvoice/jobs/` (job
lifecycle, verification, persistence) and `services/voice/clawvoice/voice/floor.py`
(announcement timing). See [docs/voice.md](../../docs/voice.md#background-jobs).

Future (Priority 2): agent registry routing by name, several agents on one
speaking floor, and structured agent-to-agent collaboration. Reuse Pipecat and
OpenClaw Gateway primitives wherever appropriate.
