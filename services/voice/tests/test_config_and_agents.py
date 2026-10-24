import copy
import json

import pytest

from clawvoice import secrets
from clawvoice.agents import ConfigError, load_registry
from clawvoice.config import MODEL, URL, Settings, load_settings
from conftest import agent_from, load_yaml


def test_preview_model_is_mandatory_and_never_falls_back():
    for model in ["higgs-realtime", "", "other"]:
        with pytest.raises(ValueError, match="No fallback"):
            Settings(api_key="k", model=model)
    for url in ["ws://api.boson.ai/v1/realtime", "wss://evil.example/v1/realtime", URL + "&model=higgs-realtime"]:
        with pytest.raises(ValueError, match="official endpoint"):
            Settings(api_key="k", url=url)
    assert Settings(api_key="k").model == MODEL
    assert "k" not in repr(Settings(api_key="k"))


def test_boson_key_precedence_keeps_key_out_of_repr(monkeypatch):
    monkeypatch.delenv("BOSON_API_KEY", raising=False)
    settings = load_settings({"BOSON_API_KEY": "process-key"})
    assert settings.api_key == "process-key"
    assert "process-key" not in repr(settings)


def test_secret_references_resolve_server_side_only(tmp_path):
    env_file = tmp_path / "runtime.env"
    env_file.write_text("OPENCLAW_GATEWAY_TOKEN=abc123secret\n")
    assert secrets.resolve(f"envfile:{env_file.name}#OPENCLAW_GATEWAY_TOKEN", root=tmp_path) == "abc123secret"
    assert secrets.resolve("env:MY_TOKEN", environ={"MY_TOKEN": "zzz"}) == "zzz"
    with pytest.raises(secrets.SecretError) as error:
        secrets.resolve("env:MISSING_TOKEN", environ={})
    assert "MISSING_TOKEN" in str(error.value)
    outside = tmp_path.parent / "outside.env"
    outside.write_text("X=1\n")
    with pytest.raises(secrets.SecretError, match="inside the repository"):
        secrets.resolve(f"envfile:{outside}#X", root=tmp_path)
    with pytest.raises(secrets.SecretError):
        secrets.resolve("literal:abc")
    link = tmp_path / "link.env"
    link.symlink_to(env_file)
    with pytest.raises(secrets.SecretError, match="symlink"):
        secrets.resolve("envfile:link.env#OPENCLAW_GATEWAY_TOKEN", root=tmp_path)


def test_tari_configuration_is_loaded_from_files_not_code():
    registry = load_registry(environ={})
    tari = registry.default
    assert tari.id == "tari" and tari.display_name == "Tari"
    assert tari.session_key == "agent:tari:clawvoice-local-owner"
    assert tari.gateway.token_ref.startswith("envfile:")
    assert [t.key for t in tari.topics] == [
        "planner.today", "planner.tomorrow", "calendar.today", "calendar.next", "checklist.focus", "jobs.recent"
    ]
    assert set(tari.receipt_types) == {"calendar_event", "planner_block", "checklist_task"}


def test_astra_configuration_is_loaded_from_files_not_code():
    registry = load_registry(environ={})
    astra = registry.get("astra")
    assert astra.id == "astra" and astra.display_name == "Astra"
    assert astra.fixture is False
    assert astra.color == "#3b82f6"
    assert astra.session_key == "agent:astra:clawvoice-local-owner"
    assert astra.gateway.ws_url == "ws://127.0.0.1:18949"
    assert astra.gateway.http_url == "http://127.0.0.1:18949"
    assert astra.gateway.token_ref == "envfile:infra/openclaw/.local-astra/runtime.env#OPENCLAW_GATEWAY_TOKEN"
    assert astra.gateway.device_file != registry.get("tari").gateway.device_file
    assert astra.plugins == ("coding_workspace",)
    assert [t.key for t in astra.topics] == [
        "projects.active", "projects.recent", "project.latest_update", "jobs.recent"
    ]
    assert set(astra.receipt_types) == {"coding_change", "test_run"}


def test_fixtures_load_only_when_enabled():
    assert list(load_registry(environ={}).agents) == ["tari", "astra"]
    assert list(load_registry(environ={"CLAWVOICE_ENABLE_FIXTURES": "1"}).agents) == ["tari", "astra", "fixture"]
    registry = load_registry(environ={"CLAWVOICE_AGENTS": "astra"})
    assert registry.default.id == "astra"


def test_public_identity_exposes_no_endpoints_or_secret_references():
    registry = load_registry(environ={"CLAWVOICE_ENABLE_FIXTURES": "1"})
    for agent in registry.all():
        text = json.dumps(agent.public_identity())
        for forbidden in ("token", "envfile", "ws://", "http://", "manage.py", "18939", "18949"):
            assert forbidden not in text


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["openclaw"]["gateway"].update(ws_url="ws://example.com:18939"), "loopback"),
        (lambda d: d["openclaw"]["gateway"].update(token_ref="plain-token"), "server-side reference"),
        (lambda d: d["openclaw"]["gateway"].update(transport="carrier-pigeon"), "transport"),
        (lambda d: d.update(timezone="Mars/Olympus"), "timezone"),
        (lambda d: d["hot_context"]["topics"].append(dict(d["hot_context"]["topics"][0])), "duplicate"),
        (lambda d: d["delegation"]["receipt_types"]["calendar_event"].update(invalidates=["nope"]), "unknown topics"),
        (lambda d: d.update(id="Bad ID"), "id must match"),
    ],
)
def test_invalid_agent_configuration_fails_early(mutate, message):
    data = copy.deepcopy(load_yaml("tari"))
    mutate(data)
    with pytest.raises(ConfigError, match=message):
        agent_from(data)


def test_adding_a_topic_is_configuration_only():
    data = copy.deepcopy(load_yaml("tari"))
    data["hot_context"]["topics"].append(
        {"key": "weather.local", "description": "local weather", "resolver": "command_json",
         "args": {"helper": "planner", "argv": ["weather", "--json"]}, "ttl_seconds": 600}
    )
    agent = agent_from(data)
    topic = agent.topic("weather.local")
    assert topic.ttl_seconds == 600 and topic.max_staleness_seconds == 900
