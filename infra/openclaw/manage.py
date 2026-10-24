#!/usr/bin/env python3
"""Owner-side manager for the isolated ClawVoice OpenClaw demo runtime.

Never reads ~/.openclaw, ~/.config/gogcli, production Tari, or any other checkout.
Run: python3 infra/openclaw/manage.py init|install-planner|planner|start|stop|status|logs|doctor|openclaw|gog|google|chat
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local"
DEFAULT_MODEL = "openai/gpt-6.1-sol"
DEFAULT_PORT = 18939
PLANNER_SECTION = "## Local day planner and checklist"


def owned_path(path: Path) -> Path:
    """Refuse symlinks throughout any local installation-owned path."""
    path = Path(path)
    if not path.is_relative_to(ROOT) or ".." in path.relative_to(ROOT).parts:
        raise RuntimeError("Refusing path outside this ClawVoice checkout")
    current = ROOT
    for part in path.relative_to(ROOT).parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("Refusing symlink in installation path")
    return path


def private_dir(path: Path) -> None:
    path = owned_path(path)
    current = ROOT
    for part in path.relative_to(ROOT).parts:
        current = current / part
        current.mkdir(exist_ok=True, mode=0o700)
        current.chmod(0o700)


def write_if_absent(path: Path, data: str, mode: int = 0o600) -> None:
    path = owned_path(path)
    private_dir(path.parent)
    if path.exists():
        if not path.is_file():
            raise RuntimeError("Expected an ordinary private file")
        path.chmod(mode)
        return
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(data)


def load_env() -> dict[str, str]:
    path = owned_path(LOCAL / "runtime.env")
    if not path.is_file():
        raise RuntimeError("Missing local configuration. Run 'init' first.")
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise RuntimeError("Invalid runtime.env line")
        if any(c in value for c in "\r\n\x00$'\""):
            raise RuntimeError("Use unquoted KEY=value syntax in runtime.env")
        data[key] = value
    if not re.fullmatch(r"clawvoice-[a-f0-9]{12}", data.get("COMPOSE_PROJECT_NAME", "")):
        raise RuntimeError("Invalid installation-specific Compose project name")
    port = int(data.get("CLAWVOICE_OPENCLAW_PORT", DEFAULT_PORT))
    if port < 1024 or port > 65535:
        raise RuntimeError("Invalid local Gateway port")
    for key in ("OPENCLAW_GATEWAY_TOKEN", "GOG_KEYRING_PASSWORD"):
        if not re.fullmatch(r"[a-f0-9]{64}", data.get(key, "")):
            raise RuntimeError("Missing or invalid generated private credential")
    return data


def initialize(timezone: str, name: str, port: int) -> None:
    if os.getuid() == 0:
        raise RuntimeError("Run init as your normal non-root Docker user")
    try:
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError):
        raise RuntimeError("Use a valid IANA timezone") from None
    if not re.fullmatch(r"[A-Za-z0-9 _-]{1,50}", name):
        raise RuntimeError("Name must use simple characters")
    if not (1024 <= port <= 65535):
        raise RuntimeError("Port must be 1024-65535")
    for rel in ("", "state", "state/workspace", "google"):
        private_dir(LOCAL / rel)
    values = {
        "COMPOSE_PROJECT_NAME": "clawvoice-" + secrets.token_hex(6),
        "CLAWVOICE_UID": str(os.getuid()),
        "CLAWVOICE_GID": str(os.getgid()),
        "CLAWVOICE_OPENCLAW_PORT": str(port),
        "CLAWVOICE_NAME": name,
        "CLAWVOICE_TIMEZONE": timezone,
        "CLAWVOICE_MODEL": DEFAULT_MODEL,
        "TARI_TIMEZONE": timezone,              # compatibility with tari-google
        "TARI_GOOGLE_ACCOUNT": "",               # set your different demo Google account
        "OPENCLAW_GATEWAY_TOKEN": secrets.token_hex(32),
        "GOG_KEYRING_PASSWORD": secrets.token_hex(32),
    }
    env_text = "# Private ClawVoice demo runtime. Never commit or share.\n"
    env_text += "".join(f"{key}={value}\n" for key, value in values.items())
    write_if_absent(LOCAL / "runtime.env", env_text)

    active_env = load_env()
    config_target = LOCAL / "state/openclaw.json"
    if not owned_path(config_target).exists():
        config = json.loads((ROOT / "config/openclaw.json").read_text(encoding="utf-8"))
        config["agents"]["defaults"]["model"] = {"primary": active_env["CLAWVOICE_MODEL"], "fallbacks": []}
        config["agents"]["defaults"]["userTimezone"] = active_env["CLAWVOICE_TIMEZONE"]
        agent = config["agents"]["entries"]["tari"]
        agent["name"] = active_env["CLAWVOICE_NAME"]
        agent["identity"]["name"] = active_env["CLAWVOICE_NAME"]
        port = active_env["CLAWVOICE_OPENCLAW_PORT"]
        config["gateway"]["controlUi"]["allowedOrigins"] = [
            f"http://localhost:{port}", f"http://127.0.0.1:{port}"
        ]
        write_if_absent(config_target, json.dumps(config, indent=2) + "\n")
    templates = ROOT / "workspace"
    count = 0
    for source in sorted(templates.rglob("*")):
        if (source.is_file() and source.suffix != ".pyc" and "__pycache__" not in source.parts
                and source.relative_to(templates).parts[0] != "planner"):
            target = owned_path(LOCAL / "state/workspace" / source.relative_to(templates))
            existed = target.exists()
            text = source.read_text(encoding="utf-8").replace(
                "{{CLAWVOICE_NAME}}", active_env["CLAWVOICE_NAME"]
            )
            write_if_absent(target, text, 0o700 if source.parent.name == "bin" else 0o600)
            count += not existed
    print(f"Initialized isolated ClawVoice state; seeded {count} workspace files.")
    print("Next: configure ChatGPT OAuth and a separate Google account (see README).")
    print("Existing private files are preserved; repeat init will not overwrite them.")


def install_planner() -> None:
    """Install only planner code/skill and append instructions; preserve user state."""
    load_env()
    workspace = owned_path(LOCAL / "state/workspace")
    if not workspace.is_dir():
        raise RuntimeError("Missing demo workspace; initialize the installation first")
    plans = []
    for relative in ("bin/day-planner", "skills/tari-day-planner/SKILL.md", "AGENTS.md", "TOOLS.md"):
        source = (ROOT / "workspace" / relative).read_text()
        target = owned_path(workspace / relative)
        existing = target.read_text() if target.exists() else None
        if relative in ("AGENTS.md", "TOOLS.md"):
            if existing is None:
                raise RuntimeError("Missing foundation instructions; restore " + relative + " before installing planner")
            section = PLANNER_SECTION + source.split(PLANNER_SECTION, 1)[1]
            if existing and PLANNER_SECTION in existing:
                if section.strip() not in existing:
                    raise RuntimeError("Existing planner instructions differ from the template; review and merge " + relative)
                continue
            updated = (existing or "").rstrip() + "\n\n" + section.rstrip() + "\n"
        else:
            if existing is not None and existing != source:
                raise RuntimeError("Existing planner code/skill differs from the template; review and merge " + relative)
            updated = source
        if existing != updated:
            plans.append((relative, target, existing, updated))
    backup_root = LOCAL / "backups" / ("planner-install-" + secrets.token_hex(8))
    for relative, target, existing, updated in plans:
        if existing is not None:
            write_if_absent(backup_root / relative, existing)
        private_dir(target.parent)
        if (target.read_text() if owned_path(target).exists() else None) != existing:
            raise RuntimeError("Workspace changed during planner installation; retry")
        temporary = target.with_name(".planner-install-" + secrets.token_hex(8))
        mode = 0o700 if relative.startswith("bin/") else 0o600
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
            with os.fdopen(fd, "w") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Installed planner: {len(plans)} workspace files changed; private state preserved.")


def compose_args(*command: str) -> tuple[list[str], dict[str, str]]:
    data = load_env()
    # Do not inherit production OpenClaw, Tari, Google or other model credentials.
    process_env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("COMPOSE_", "OPENCLAW_", "TARI_", "GOG_",
                                        "CLAWVOICE_", "OPENAI_", "BOSON_", "CODEX_"))}
    process_env.update(data)
    cmd = [
        "docker", "compose", "--project-directory", str(ROOT),
        "--env-file", str(LOCAL / "runtime.env"),
        "-f", str(ROOT / "compose.yaml"),
        "-p", data["COMPOSE_PROJECT_NAME"], *command,
    ]
    return cmd, process_env


def run_compose(*args: str) -> int:
    cmd, env = compose_args(*args)
    return subprocess.call(cmd, cwd=ROOT, env=env)


def check_gateway() -> None:
    data = load_env()
    base = f"http://127.0.0.1:{data['CLAWVOICE_OPENCLAW_PORT']}"
    try:
        with urllib.request.urlopen(base + "/healthz", timeout=10) as response:
            if response.status != 200:
                raise RuntimeError("Gateway health check failed")
        # A session key and agent selector must never substitute for auth.
        request = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps({"model": "openclaw/tari", "user": "unauthenticated-check",
                             "messages": [{"role": "user", "content": "auth boundary check"}]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10):
                raise RuntimeError("Gateway accepted a request without authentication")
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise RuntimeError("Gateway authentication boundary check failed") from None
        request = urllib.request.Request(
            base + "/v1/chat/completions", data=b"{}",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + data["OPENCLAW_GATEWAY_TOKEN"]}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10):
                raise RuntimeError("Gateway accepted an invalid chat request")
        except urllib.error.HTTPError as error:
            if error.code != 400:
                raise RuntimeError("Authenticated Gateway route check failed") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("Local Gateway is unavailable") from None
    print("Gateway health, auth rejection, and authenticated route: passed", flush=True)


def chat(prompt: str, conversation: str) -> None:
    data = load_env()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", conversation):
        raise RuntimeError("Use a short opaque conversation ID")
    target = f"http://127.0.0.1:{data['CLAWVOICE_OPENCLAW_PORT']}/v1/chat/completions"
    body = json.dumps({
        "model": "openclaw/tari",  # OpenClaw agent selector, not provider model
        "user": conversation,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        target, data=body, method="POST",
        headers={"Authorization": "Bearer " + data["OPENCLAW_GATEWAY_TOKEN"],
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Gateway returned HTTP {exc.code}; check local model auth and logs") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Gateway not reachable or request timed out; outcome may be unknown") from None
    try:
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError()
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError("Gateway returned no verifiable assistant response") from None
    print(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("--timezone", default="America/Toronto")
    init.add_argument("--name", default="Tari")
    init.add_argument("--port", type=int, default=DEFAULT_PORT)
    for action in ("install-planner", "planner", "start", "stop", "status", "logs", "doctor", "openclaw", "gog", "google"):
        sub.add_parser(action, add_help=action not in ("planner", "openclaw", "gog", "google"))
    speak = sub.add_parser("chat")
    speak.add_argument("prompt")
    speak.add_argument("--conversation", default="clawvoice-demo")
    args, rest = parser.parse_known_args()
    if args.action == "init":
        if rest:
            parser.error("Unexpected arguments")
        initialize(args.timezone, args.name, args.port)
        return 0
    if args.action == "start":
        return run_compose("up", "-d", "--build", "--wait", "--wait-timeout", "90", "openclaw")
    if args.action == "install-planner":
        if rest:
            parser.error("Unexpected arguments")
        install_planner()
        return 0
    if args.action == "planner":
        return run_compose("exec", "-T", "openclaw", "./bin/day-planner", *rest)
    if args.action == "stop":
        return run_compose("stop", "openclaw")
    if args.action == "status":
        return run_compose("ps")
    if args.action == "logs":
        return run_compose("logs", "--tail", "100", "openclaw")
    if args.action == "doctor":
        if rest:
            parser.error("Unexpected arguments")
        data = load_env()
        if not shutil.which("docker"):
            raise RuntimeError("Docker CLI is missing")
        print("Isolated project:", data["COMPOSE_PROJECT_NAME"])
        print("Local Gateway port:", data["CLAWVOICE_OPENCLAW_PORT"])
        print("Google account configured:", bool(data.get("TARI_GOOGLE_ACCOUNT")))
        for command in (("config", "--quiet"),
                        ("exec", "-T", "openclaw", "node", "/app/openclaw.mjs", "config", "validate", "--json"),
                        ("exec", "-T", "openclaw", "gog", "--version")):
            status = run_compose(*command)
            if status:
                return status
        check_gateway()
        return run_compose("ps")
    if args.action == "openclaw":
        if not rest:
            parser.error("Pass OpenClaw CLI arguments")
        return run_compose("exec", "openclaw", "node", "/app/openclaw.mjs", *rest)
    if args.action == "gog":
        if not rest:
            parser.error("Pass gog CLI arguments")
        return run_compose("exec", "openclaw", "gog", *rest)
    if args.action == "google":
        if not rest:
            parser.error("Pass calendar/gmail helper arguments")
        return run_compose("exec", "-T", "openclaw", "./bin/tari-google", *rest)
    if args.action == "chat":
        if rest:
            parser.error("Unexpected arguments")
        chat(args.prompt, args.conversation)
        return 0
    raise RuntimeError("Unknown command")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print("ClawVoice setup: " + str(error), file=sys.stderr)
        sys.exit(1)
