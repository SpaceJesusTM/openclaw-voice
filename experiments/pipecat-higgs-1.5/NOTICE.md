# Source attribution

The runner/pipeline composition in `bot.py` and WebRTC/RTVI probe approach in
`probe.py` adapt patterns from Boson AI's
[Higgs Realtime Tutorial preview branch](https://github.com/boson-ai/Higgs-Realtime-Tutorial/tree/f8d23e7eaacf412a175522f5996e5eb3a4d9a42e/pipecat),
distributed under the Apache License 2.0. A copy of that license is included at
[licenses/Apache-2.0.txt](licenses/Apache-2.0.txt).

Changes for this experiment include strict preview configuration and resolved
model validation, shared local environment loading, the demo planner, optional
mock worker jobs, sanitized traces, the two compatibility workarounds, stronger
probe evidence/timeout checks, decoded-media validation, synthetic speech and
barge-in tests, comparison tooling, and automated tests.

Pipecat and pipecat-boson are installed dependencies with their upstream BSD
2-Clause licenses. Their package source is not vendored or modified here.
