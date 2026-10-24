#!/usr/bin/env python3
"""Owner-side manager for the isolated ClawVoice OpenClaw demo runtime.

Never reads ~/.openclaw, ~/.config/gogcli, production Tari, or any other checkout.
Run: python3 infra/openclaw/manage.py [--agent tari|astra] init|install-planner|planner|project|start|stop|status|logs|doctor|openclaw|gog|google|chat
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
ASTRA_LOCAL = ROOT / ".local-astra"
ASTRA_WORKSPACE = ROOT.parent.parent / "astra-workspace"
# Fallback home for Astra's OpenClaw state and workspace when the checkout's filesystem
# cannot keep private modes (e.g. a Windows drive under WSL, where every file reads as 0777).
ASTRA_EXTERNAL_HOME = Path.home() / ".local/share/clawvoice"
DEFAULT_MODEL = "openai/gpt-6.1-sol"
DEFAULT_PORT = 18939
ASTRA_DEFAULT_PORT = 18949
ASTRA_DEFAULT_PREVIEW_PORT = 18950
PLANNER_SECTION = "## Local day planner and checklist"


def owned_path(path: Path, root: Path | None = None) -> Path:
    """Refuse symlinks throughout any local installation-owned path."""
    root = root or ROOT
    path = Path(path)
    if not path.is_relative_to(root) or ".." in path.relative_to(root).parts:
        raise RuntimeError("Refusing path outside this ClawVoice checkout")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("Refusing symlink in installation path")
    return path


def owned_workspace_path(path: Path, workspace_root: Path | None = None) -> Path:
    """Refuse symlinks or escapes outside the dedicated Astra workspace root."""
    ws_root = Path(workspace_root or ASTRA_WORKSPACE)
    repo_root = ROOT.parent.parent if ws_root.is_relative_to(ROOT.parent.parent) else ws_root.parent
    path = Path(path)
    if not path.is_relative_to(repo_root) or ".." in path.relative_to(repo_root).parts:
        raise RuntimeError("Refusing workspace path outside this ClawVoice checkout")
    if not (path == ws_root or path.is_relative_to(ws_root)):
        raise RuntimeError("Refusing path outside dedicated Astra workspace")
    current = repo_root
    for part in path.relative_to(repo_root).parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("Refusing symlink in Astra workspace path")
    return path


def local_dir_for(agent: str = "tari") -> Path:
    if agent == "astra":
        return ASTRA_LOCAL if ASTRA_LOCAL.is_relative_to(ROOT) else (ROOT / ".local-astra")
    return LOCAL


def astra_workspace_dir() -> Path:
    override = os.environ.get("ASTRA_WORKSPACE_DIR")
    if override:
        return Path(override).resolve()
    try:
        configured = load_env("astra").get("ASTRA_WORKSPACE_DIR")
    except RuntimeError:
        configured = None
    if configured:
        return Path(configured)
    if ASTRA_WORKSPACE.parent == ROOT.parent.parent:
        return ASTRA_WORKSPACE
    return ROOT / "astra-workspace"


def private_dir(path: Path, root: Path | None = None) -> None:
    root = root or ROOT
    path = owned_path(path, root)
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        current.mkdir(exist_ok=True, mode=0o700)
        current.chmod(0o700)


def keeps_private_mode(directory: Path) -> bool:
    return directory.stat().st_mode & 0o777 == 0o700


def astra_state_dir(data: dict[str, str]) -> Path:
    override = data.get("ASTRA_STATE_DIR")
    return Path(override) if override else local_dir_for("astra") / "state"


def workspace_dir_create(path: Path, workspace_root: Path) -> None:
    path = owned_workspace_path(path, workspace_root)
    ws_root = owned_workspace_path(workspace_root, workspace_root)
    ws_root.mkdir(parents=True, exist_ok=True)
    current = ws_root
    for part in path.relative_to(ws_root).parts:
        current = current / part
        current.mkdir(exist_ok=True, mode=0o755)


def write_if_absent(path: Path, data: str, mode: int = 0o600, root: Path | None = None) -> None:
    path = owned_path(path, root)
    private_dir(path.parent, root)
    if path.exists():
        if not path.is_file():
            raise RuntimeError("Expected an ordinary private file")
        path.chmod(mode)
        return
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(data)


def write_workspace_file_if_absent(
    path: Path, data: str, workspace_root: Path, mode: int = 0o644
) -> bool:
    path = owned_workspace_path(path, workspace_root)
    workspace_dir_create(path.parent, workspace_root)
    if path.exists():
        if not path.is_file():
            raise RuntimeError("Expected an ordinary workspace file")
        path.chmod(mode)
        return False
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(data)
    return True


def load_env(agent: str = "tari") -> dict[str, str]:
    loc = local_dir_for(agent)
    path = owned_path(loc / "runtime.env")
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
    prefix = r"clawvoice-astra-[a-f0-9]{12}" if agent == "astra" else r"clawvoice-[a-f0-9]{12}"
    if not re.fullmatch(prefix, data.get("COMPOSE_PROJECT_NAME", "")):
        raise RuntimeError("Invalid installation-specific Compose project name")
    default_port = ASTRA_DEFAULT_PORT if agent == "astra" else DEFAULT_PORT
    port = int(data.get("CLAWVOICE_OPENCLAW_PORT", default_port))
    if port < 1024 or port > 65535:
        raise RuntimeError("Invalid local Gateway port")
    required_keys = ("OPENCLAW_GATEWAY_TOKEN",) if agent == "astra" else ("OPENCLAW_GATEWAY_TOKEN", "GOG_KEYRING_PASSWORD")
    for key in required_keys:
        if not re.fullmatch(r"[a-f0-9]{64}", data.get(key, "")):
            raise RuntimeError("Missing or invalid generated private credential")
    for key in ("ASTRA_STATE_DIR", "ASTRA_WORKSPACE_DIR"):
        if key in data and not Path(data[key]).is_absolute():
            raise RuntimeError(f"{key} must be an absolute path")
    return data


def initialize(
    timezone: str,
    name: str,
    port: int,
    agent: str = "tari",
    preview_port: int = ASTRA_DEFAULT_PREVIEW_PORT,
) -> None:
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

    if agent == "astra":
        if not (1024 <= preview_port <= 65535) or preview_port == port:
            raise RuntimeError("Preview port must be 1024-65535 and distinct from Gateway port")
        loc = local_dir_for("astra")
        for rel in ("", "state"):
            private_dir(loc / rel)
        values = {
            "COMPOSE_PROJECT_NAME": "clawvoice-astra-" + secrets.token_hex(6),
            "CLAWVOICE_UID": str(os.getuid()),
            "CLAWVOICE_GID": str(os.getgid()),
            "CLAWVOICE_OPENCLAW_PORT": str(port),
            "ASTRA_PREVIEW_PORT": str(preview_port),
            "CLAWVOICE_NAME": name,
            "CLAWVOICE_TIMEZONE": timezone,
            "CLAWVOICE_MODEL": DEFAULT_MODEL,
            "OPENCLAW_GATEWAY_TOKEN": secrets.token_hex(32),
        }
        env_text = "# Private ClawVoice Astra coding agent runtime. Never commit or share.\n"
        env_text += "".join(f"{key}={value}\n" for key, value in values.items())
        write_if_absent(loc / "runtime.env", env_text)

        active_env = load_env("astra")
        if not keeps_private_mode(loc / "state"):
            # OpenClaw rejects both its state and its workspace on such a filesystem.
            external = ASTRA_EXTERNAL_HOME / active_env["COMPOSE_PROJECT_NAME"]
            moved = {"ASTRA_STATE_DIR": external / "state", "ASTRA_WORKSPACE_DIR": external / "workspace"}
            with owned_path(loc / "runtime.env").open("a", encoding="utf-8") as handle:
                for key, path in moved.items():
                    if key not in active_env:
                        handle.write(f"{key}={path}\n")
            active_env = load_env("astra")
        state_dir = astra_state_dir(active_env)
        state_root = state_dir.parent if "ASTRA_STATE_DIR" in active_env else None
        if state_root:
            state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            state_root.chmod(0o700)
            private_dir(state_dir, state_root)
            if not keeps_private_mode(state_dir):
                raise RuntimeError("ASTRA_STATE_DIR cannot keep private permissions")
        config_target = state_dir / "openclaw.json"
        if not owned_path(config_target, state_root).exists():
            config = json.loads((ROOT / "config/openclaw-astra.json").read_text(encoding="utf-8"))
            config["agents"]["defaults"]["model"] = {"primary": active_env["CLAWVOICE_MODEL"], "fallbacks": []}
            config["agents"]["defaults"]["userTimezone"] = active_env["CLAWVOICE_TIMEZONE"]
            entry = config["agents"]["entries"]["astra"]
            entry["name"] = active_env["CLAWVOICE_NAME"]
            entry["identity"]["name"] = active_env["CLAWVOICE_NAME"]
            active_port = active_env["CLAWVOICE_OPENCLAW_PORT"]
            config["gateway"]["controlUi"]["allowedOrigins"] = [
                f"http://localhost:{active_port}", f"http://127.0.0.1:{active_port}"
            ]
            write_if_absent(config_target, json.dumps(config, indent=2) + "\n", root=state_root)

        ws_root = astra_workspace_dir()
        for subdir in ("projects", "project-state", "logs"):
            workspace_dir_create(ws_root / subdir, ws_root)
        templates = ROOT / "workspace-astra"
        count = 0
        for source in sorted(templates.rglob("*")):
            if source.is_file() and source.suffix != ".pyc" and "__pycache__" not in source.parts:
                rel = source.relative_to(templates)
                target = ws_root / rel
                text = source.read_text(encoding="utf-8").replace(
                    "{{CLAWVOICE_NAME}}", active_env["CLAWVOICE_NAME"]
                )
                mode = 0o755 if source.parent.name == "bin" else 0o644
                created = write_workspace_file_if_absent(target, text, ws_root, mode=mode)
                count += int(created)
        print(f"Initialized isolated Astra OpenClaw state; seeded {count} workspace files in {ws_root}.")
        print("Next: start the Astra container and authenticate OpenAI/Codex OAuth for agent 'astra'.")
        print("Existing private files and projects are preserved; repeat init will not overwrite them.")
        return

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
        agent_entry = config["agents"]["entries"]["tari"]
        agent_entry["name"] = active_env["CLAWVOICE_NAME"]
        agent_entry["identity"]["name"] = active_env["CLAWVOICE_NAME"]
        port_str = active_env["CLAWVOICE_OPENCLAW_PORT"]
        config["gateway"]["controlUi"]["allowedOrigins"] = [
            f"http://localhost:{port_str}", f"http://127.0.0.1:{port_str}"
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


def update_workspace(agent: str = "tari") -> None:
    """Upgrade known P1 templates only; back up files and never touch planner data."""
    import hashlib
    if agent == "astra":
        active_env = load_env("astra")
        ws_root = astra_workspace_dir()
        templates = ROOT / "workspace-astra"
        updated_count = 0
        for source in sorted(templates.rglob("*")):
            if source.is_file() and source.suffix != ".pyc" and "__pycache__" not in source.parts:
                rel = source.relative_to(templates)
                target = owned_workspace_path(ws_root / rel, ws_root)
                workspace_dir_create(target.parent, ws_root)
                new_text = source.read_text(encoding="utf-8").replace(
                    "{{CLAWVOICE_NAME}}", active_env["CLAWVOICE_NAME"]
                )
                old_text = target.read_text(encoding="utf-8") if target.exists() else None
                if old_text == new_text:
                    continue
                mode = 0o755 if source.parent.name == "bin" else 0o644
                tmp = target.with_name(".astra-update-" + secrets.token_hex(6))
                try:
                    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(new_text)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, target)
                    updated_count += 1
                finally:
                    tmp.unlink(missing_ok=True)
        print(f"Updated {updated_count} Astra workspace template files; projects and state untouched.")
        return

    load_env()
    # Accepted earlier template versions per file: the first P1 release and the
    # P1 fixes release (5a78c91). Anything else is an owner customization.
    known = {
        'bin/tari-google': ('f50891ef7d368548801ce08b7c94ebe3a7b5082e9dc631af6b7b5dbeb68d3e52',
                           '3a0a573cdb510714a8a05f224ba8674497ac958ab797140cee171d60de222df8'),
        'bin/day-planner': ('e71872162ebc7683627ba82b6de915d2493710baf0caa8bffef618365258d477',
                           '311a2b33f43c04d8437ec3a615993b35d275cd9e2e0d58c7675298383a08f26b'),
        'AGENTS.md': ('93c176901088aed8babf2212e7eeedf51baeadba1dac0dc6bb0948992499f5b3',
                     '66e7b70c2972dcba7d7409821ab7e5771624bb748057b873f78fe20438e5421a'),
        'TOOLS.md': ('21d38358da6a33b1c07301c2e6c2db696d5ce1fb07e8efec3a60ef8c04278790',
                    '70cc97fcc777a2056241ad7ff02763fc4d2e1230d8f8d8f7720e6f148a2287c6'),
        'skills/tari-gmail/SKILL.md': ('020db9e7728da8b744d8c71ceb989083b280dd7b4d1abdbfee2b5ced47adbac2',
                                      '116218daa6407d7f69aeaf1921977a058044cb8179c337048bdacd64c44b2cf1'),
        'skills/tari-gmail/references/retrieval.md': ('919cc0d73cc63ee28fe1fb349fbe2241b31e2c8f89fef9c0ae6de3ded638587d',
                                                     'bc7c8d315a6c6781e3e95b142dcdef464d40ffdbdff5d0e08df83408de6e4730'),
        'skills/tari-calendar/SKILL.md': ('f8bab884852b9ac4dda75898cc3e51c6c8e7245997506591be0b856007fddb1c',
                                         'e609def4a69de3b133d0da520690fbc1197bd98912b8a50849b6cbfb6886b3a5'),
        'skills/tari-day-planner/SKILL.md': ('37f8c6b610f70027f080493d0d980f86eb5e8b425479ccc2783ed52a6c175639',
                                            '9b38256929c3af695f572ba00ef32e3776364596d2957c4a6838ecea6fba0dee'),
    }
    workspace = owned_path(LOCAL / "state/workspace")
    plans = []
    for relative, previous in known.items():
        target = owned_path(workspace / relative)
        old = target.read_text()
        new = (ROOT / "workspace" / relative).read_text()
        if old == new:
            continue
        if hashlib.sha256(old.encode()).hexdigest() not in previous:
            raise RuntimeError("Customized workspace file needs manual merge: " + relative +
                               ". No files changed; planner state is preserved.")
        plans.append((relative, target, old, new))
    backup = LOCAL / "backups" / ("p1-fixes-" + secrets.token_hex(8))
    for relative, target, old, new in plans:
        write_if_absent(backup / relative, old)
        if target.read_text() != old:
            raise RuntimeError("Workspace changed during update; inspect backups before retrying")
        temporary = target.with_name(".p1-update-" + secrets.token_hex(8))
        try:
            write_if_absent(temporary, new, 0o700 if relative.startswith("bin/") else 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Updated {len(plans)} known workspace templates; backups saved locally; planner data untouched.")


def compose_args(*command: str, agent: str = "tari") -> tuple[list[str], dict[str, str]]:
    data = load_env(agent)
    loc = local_dir_for(agent)
    compose_file = ROOT / ("compose.astra.yaml" if agent == "astra" else "compose.yaml")
    # Do not inherit production OpenClaw, Tari, Google or other model credentials.
    process_env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("COMPOSE_", "OPENCLAW_", "TARI_", "GOG_",
                                        "CLAWVOICE_", "OPENAI_", "BOSON_", "CODEX_"))}
    process_env.update(data)
    cmd = [
        "docker", "compose", "--project-directory", str(ROOT),
        "--env-file", str(loc / "runtime.env"),
        "-f", str(compose_file),
        "-p", data["COMPOSE_PROJECT_NAME"], *command,
    ]
    return cmd, process_env


def run_compose(*args: str, agent: str = "tari") -> int:
    cmd, env = compose_args(*args, agent=agent)
    return subprocess.call(cmd, cwd=ROOT, env=env)


def run_astra_project(*args: str) -> int:
    ws_root = astra_workspace_dir()
    seeded_helper = ws_root / "bin/astra-project"
    template_helper = ROOT / "workspace-astra/bin/astra-project"
    helper_path = seeded_helper if seeded_helper.is_file() else template_helper
    if not helper_path.is_file():
        raise RuntimeError("Missing astra-project helper binary")
    env = dict(os.environ)
    env["ASTRA_WORKSPACE_DIR"] = str(ws_root)
    try:
        data = load_env("astra")
        if "ASTRA_PREVIEW_PORT" in data:
            env["ASTRA_PREVIEW_PORT"] = data["ASTRA_PREVIEW_PORT"]
    except RuntimeError:
        env.setdefault("ASTRA_PREVIEW_PORT", str(ASTRA_DEFAULT_PREVIEW_PORT))
    return subprocess.call([sys.executable, str(helper_path), *args], env=env)


def check_gateway(agent: str = "tari") -> None:
    data = load_env(agent)
    base = f"http://127.0.0.1:{data['CLAWVOICE_OPENCLAW_PORT']}"
    model_selector = f"openclaw/{agent}"
    try:
        with urllib.request.urlopen(base + "/healthz", timeout=10) as response:
            if response.status != 200:
                raise RuntimeError("Gateway health check failed")
        # A session key and agent selector must never substitute for auth.
        request = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps({"model": model_selector, "user": "unauthenticated-check",
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


def chat(prompt: str, conversation: str, agent: str = "tari") -> None:
    data = load_env(agent)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", conversation):
        raise RuntimeError("Use a short opaque conversation ID")
    target = f"http://127.0.0.1:{data['CLAWVOICE_OPENCLAW_PORT']}/v1/chat/completions"
    body = json.dumps({
        "model": f"openclaw/{agent}",  # OpenClaw agent selector, not provider model
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
    parser.add_argument("--agent", choices=("tari", "astra"), default="tari")
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("--timezone", default="America/Toronto")
    init.add_argument("--name", default=None)
    init.add_argument("--port", type=int, default=None)
    init.add_argument("--preview-port", type=int, default=ASTRA_DEFAULT_PREVIEW_PORT)
    for action in ("update-workspace", "install-planner", "planner", "project", "start", "stop", "status", "logs", "doctor", "openclaw", "gog", "google"):
        sub.add_parser(action, add_help=action not in ("planner", "project", "openclaw", "gog", "google"))
    speak = sub.add_parser("chat")
    speak.add_argument("prompt")
    speak.add_argument("--conversation", default="clawvoice-demo")
    args, rest = parser.parse_known_args()
    agent = args.agent
    if args.action == "init":
        if rest:
            parser.error("Unexpected arguments")
        default_name = "Astra" if agent == "astra" else "Tari"
        default_port = ASTRA_DEFAULT_PORT if agent == "astra" else DEFAULT_PORT
        initialize(
            args.timezone,
            args.name or default_name,
            args.port if args.port is not None else default_port,
            agent=agent,
            preview_port=args.preview_port,
        )
        return 0
    if args.action == "start":
        return run_compose("up", "-d", "--build", "--wait", "--wait-timeout", "90", "openclaw", agent=agent)
    if args.action == "update-workspace":
        if rest:
            parser.error("Unexpected arguments")
        update_workspace(agent=agent)
        return 0
    if args.action == "install-planner":
        if rest or agent != "tari":
            parser.error("install-planner applies only to Tari with no extra arguments")
        install_planner()
        return 0
    if args.action == "planner":
        return run_compose("exec", "-T", "openclaw", "./bin/day-planner", *rest, agent="tari")
    if args.action == "project":
        return run_astra_project(*rest)
    if args.action == "stop":
        return run_compose("stop", "openclaw", agent=agent)
    if args.action == "status":
        return run_compose("ps", agent=agent)
    if args.action == "logs":
        return run_compose("logs", "--tail", "100", "openclaw", agent=agent)
    if args.action == "doctor":
        if rest:
            parser.error("Unexpected arguments")
        data = load_env(agent)
        if not shutil.which("docker"):
            raise RuntimeError("Docker CLI is missing")
        print("Isolated project:", data["COMPOSE_PROJECT_NAME"])
        print("Agent:", agent)
        print("Local Gateway port:", data["CLAWVOICE_OPENCLAW_PORT"])
        if agent == "astra":
            print("Local Preview port:", data.get("ASTRA_PREVIEW_PORT", ASTRA_DEFAULT_PREVIEW_PORT))
            print("Astra workspace:", astra_workspace_dir())
        else:
            print("Google account configured:", bool(data.get("TARI_GOOGLE_ACCOUNT")))
        commands: list[tuple[str, ...]] = [
            ("config", "--quiet"),
            ("exec", "-T", "openclaw", "node", "/app/openclaw.mjs", "config", "validate", "--json"),
        ]
        if agent == "tari":
            commands.append(("exec", "-T", "openclaw", "gog", "--version"))
        for command in commands:
            status = run_compose(*command, agent=agent)
            if status:
                return status
        check_gateway(agent=agent)
        return run_compose("ps", agent=agent)
    if args.action == "openclaw":
        if not rest:
            parser.error("Pass OpenClaw CLI arguments")
        return run_compose("exec", "openclaw", "node", "/app/openclaw.mjs", *rest, agent=agent)
    if args.action == "gog":
        if not rest:
            parser.error("Pass gog CLI arguments")
        return run_compose("exec", "openclaw", "gog", *rest, agent="tari")
    if args.action == "google":
        if not rest:
            parser.error("Pass calendar/gmail helper arguments")
        return run_compose("exec", "-T", "openclaw", "./bin/tari-google", *rest, agent="tari")
    if args.action == "chat":
        if rest:
            parser.error("Unexpected arguments")
        chat(args.prompt, args.conversation, agent=agent)
        return 0
    raise RuntimeError("Unknown command")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print("ClawVoice setup: " + str(error), file=sys.stderr)
        sys.exit(1)
