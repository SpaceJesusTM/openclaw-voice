UV ?= .bootstrap/bin/uv

.PHONY: setup dev check live-probe worker-demo
setup:
	python3 -m venv .bootstrap
	.bootstrap/bin/python -m pip install uv==0.13.5
	$(UV) sync --locked --python 3.11

dev:
	$(UV) run --locked bot.py -t webrtc --host 127.0.0.1 --port 7860

check:
	$(UV) run --locked python -m pytest -q
	$(UV) run --locked ruff check bot.py config.py telemetry.py service.py planner.py mock_worker.py text_bridge.py probe.py probe_audio.py tests
	$(UV) run --locked python -m compileall -q bot.py config.py telemetry.py service.py planner.py mock_worker.py text_bridge.py probe.py probe_audio.py tests

live-probe:
	$(UV) run --locked probe.py --live

worker-demo:
	CLAWVOICE_MOCK_WORKER=true BOSON_WEB_SEARCH=true $(UV) run --locked bot.py -t webrtc --host 127.0.0.1 --port 7860
