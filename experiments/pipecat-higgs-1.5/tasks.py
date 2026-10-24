"""Cross-platform tasks for macOS, Linux, and Windows; standard library only.

python tasks.py setup | dev | check | live-probe [probe args] | worker-demo | test-audio
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOOTSTRAP = ROOT / ".bootstrap"
WINDOWS = os.name == "nt"
BIN = BOOTSTRAP / ("Scripts" if WINDOWS else "bin")
UV = BIN / ("uv.exe" if WINDOWS else "uv")
UV_VERSION = "0.13.5"
SERVE = ["bot.py", "-t", "webrtc", "--host", "127.0.0.1", "--port", "7860"]
SOURCES = [
    "bot.py",
    "config.py",
    "telemetry.py",
    "service.py",
    "planner.py",
    "mock_worker.py",
    "text_bridge.py",
    "probe.py",
    "probe_audio.py",
    "tasks.py",
    "tests",
]
TEST_AUDIO = {
    "planner-input.wav": "What is on my planner today?",
    "interrupt-input.wav": "Stop counting. Say interruption confirmed.",
}
# 48 kHz 16-bit mono WAV through the built-in Windows speech synthesizer.
WINDOWS_SPEECH = """
Add-Type -AssemblyName System.Speech
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo 48000, `
  ([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen), ([System.Speech.AudioFormat.AudioChannel]::Mono)
$speech.SetOutputToWaveFile($env:TEST_AUDIO_PATH, $format)
$speech.Speak($env:TEST_AUDIO_TEXT)
$speech.Dispose()
"""


def run(*args, **env):
    environment = {
        **os.environ,
        # Keep uv's Python beside uv; Windows links into the shared uv store can fail.
        "UV_PYTHON_INSTALL_DIR": str(BOOTSTRAP / "python"),
        # Pipecat's banner and model text are not representable in legacy Windows code pages.
        "PYTHONUTF8": "1",
        **{key: str(value) for key, value in env.items()},
    }
    process = subprocess.Popen([str(arg) for arg in args], cwd=ROOT, env=environment)
    try:
        code = process.wait()
    except KeyboardInterrupt:
        # The child received the same Ctrl+C; let it shut down cleanly.
        code = process.wait()
    if code:
        raise SystemExit(code)


def uv_run(*args, **env):
    if not UV.exists():
        raise SystemExit("uv is not installed yet: run `python tasks.py setup` first")
    run(UV, "run", "--locked", *args, **env)


def setup(_):
    if not UV.exists():
        run(sys.executable, "-m", "venv", BOOTSTRAP)
        run(BIN / "python", "-m", "pip", "install", f"uv=={UV_VERSION}")
    run(UV, "sync", "--locked", "--python", "3.11")


def dev(_):
    uv_run(*SERVE)


def check(_):
    uv_run("python", "-m", "pytest", "-q")
    uv_run("ruff", "check", *SOURCES)
    uv_run("python", "-m", "compileall", "-q", *SOURCES)


def live_probe(args):
    uv_run("probe.py", "--live", *args)


def worker_demo(_):
    uv_run(*SERVE, CLAWVOICE_MOCK_WORKER="true", BOSON_WEB_SEARCH="true")


def test_audio(_):
    captures = ROOT / "captures"
    captures.mkdir(exist_ok=True)
    for name, text in TEST_AUDIO.items():
        path = captures / name
        if WINDOWS:
            run(
                "powershell",
                "-NoProfile",
                "-Command",
                WINDOWS_SPEECH,
                TEST_AUDIO_PATH=path,
                TEST_AUDIO_TEXT=text,
            )
        elif sys.platform == "darwin":
            run("say", "-o", path, "--file-format=WAVE", "--data-format=LEI16@48000", text)
        else:
            raise SystemExit(f"No built-in speech synthesizer here; record {path} saying: {text}")
        print(f"Wrote {path}")


TASKS = {
    "setup": setup,
    "dev": dev,
    "check": check,
    "live-probe": live_probe,
    "worker-demo": worker_demo,
    "test-audio": test_audio,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in TASKS:
        raise SystemExit(f"Usage: python tasks.py <{' | '.join(TASKS)}> [args]")
    TASKS[sys.argv[1]](sys.argv[2:])
