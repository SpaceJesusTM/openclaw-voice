# Thin aliases for tasks.py, which also runs without make (for example on Windows).
ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif

.PHONY: setup dev check live-probe worker-demo test-audio
setup dev check live-probe worker-demo test-audio:
	$(PYTHON) tasks.py $@
