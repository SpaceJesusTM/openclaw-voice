UV ?= .bootstrap/bin/uv
HOST ?= 127.0.0.1
PORT ?= 7860
FIXTURE_TOKEN ?= fixture-demo-token-not-a-secret

.PHONY: setup dev check live-probe ui-probe gateway-status fixture-gateway fixture-dev

setup:
	python3 -m venv .bootstrap
	.bootstrap/bin/python -m pip install uv==0.13.5
	$(UV) sync --locked --python 3.11

# ClawVoice with the configured agents (Tari by default) at http://127.0.0.1:7860
dev:
	$(UV) run --locked python -m clawvoice.server --host $(HOST) --port $(PORT)

# Offline only: no Boson, OpenClaw, Docker or Google calls.
check:
	$(UV) run --locked python -m pytest -q
	$(UV) run --locked ruff check clawvoice tests probes
	$(UV) run --locked python -m compileall -q clawvoice tests probes

# Live acceptance probes against a running `make dev` (uses Boson and OpenClaw).
live-probe:
	$(UV) run --locked python probes/voice_probe.py --live --scenario all

ui-probe:
	node probes/ui_probe.mjs --live

gateway-status:
	curl -s http://$(HOST):$(PORT)/api/state | python3 -m json.tool | head -40

# Portability demo: a fixture agent (Astra) behind a fake Gateway. No real agent work.
fixture-gateway:
	CLAWVOICE_FIXTURE_GATEWAY_TOKEN=$(FIXTURE_TOKEN) $(UV) run --locked python -m clawvoice.testing.fake_gateway --port 18999

fixture-dev:
	CLAWVOICE_AGENTS=astra CLAWVOICE_FIXTURE_GATEWAY_TOKEN=$(FIXTURE_TOKEN) \
	  $(UV) run --locked python -m clawvoice.server --host $(HOST) --port $(PORT)
