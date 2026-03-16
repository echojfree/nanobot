from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mini_nanobot.agent import AgentLoop, GatewayRuntime
from mini_nanobot.bus import InboundMessage, MessageBus
from mini_nanobot.cli import app
from mini_nanobot.config import AppConfig, load_config, save_config
from mini_nanobot.providers import DemoProvider
from mini_nanobot.tools import WriteFileTool, build_default_tool_registry
from mini_nanobot.workspace import sync_workspace_templates

runner = CliRunner()


def fake_home_env(home: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    text = str(home)
    drive = home.drive
    home_path = text[len(drive) :] if drive else text
    env = os.environ.copy()
    env.update(
        {
            "HOME": text,
            "USERPROFILE": text,
            "HOMEDRIVE": drive,
            "HOMEPATH": home_path,
        }
    )
    return env


def test_load_config_accepts_utf8_bom(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        "\ufeff" + json.dumps({"provider": {"type": "demo"}}),
        encoding="utf-8",
    )

    config, resolved = load_config(config_path)

    assert resolved == config_path.resolve()
    assert config.provider.type == "demo"


def test_onboard_and_status_are_stable(tmp_path: Path) -> None:
    env = fake_home_env(tmp_path / "home")

    onboard = runner.invoke(app, ["onboard"], env=env)
    assert onboard.exit_code == 0, onboard.output
    assert "mini-nanobot" in onboard.output

    config_path = Path(env["HOME"]) / ".mini_nanobot" / "config.json"
    workspace_path = Path(env["HOME"]) / ".mini_nanobot" / "workspace"
    assert config_path.exists()
    assert workspace_path.exists()

    status = runner.invoke(app, ["status"], env=env)
    assert status.exit_code == 0, status.output
    assert "Provider" in status.output
    assert "demo" in status.output


def test_onboard_replaces_invalid_config_cleanly(tmp_path: Path) -> None:
    env = fake_home_env(tmp_path / "home")
    config_path = tmp_path / "broken-config.json"
    config_path.write_text("{ this is not valid json", encoding="utf-8")

    result = runner.invoke(app, ["onboard", "-c", str(config_path)], env=env)

    assert result.exit_code == 0, result.output
    assert "Replacing invalid config with defaults" in result.output

    config, _ = load_config(config_path)
    assert config.provider.type == "demo"


def test_agent_demo_tool_loop_persists_session_history(tmp_path: Path) -> None:
    env = fake_home_env(tmp_path / "home")

    first = runner.invoke(app, ["agent", "-m", "/write notes/todo.txt :: hello"], env=env)
    assert first.exit_code == 0, first.output
    assert "write_file" in first.output
    assert "Wrote 5 characters" in first.output

    second = runner.invoke(app, ["agent", "-m", "/edit notes/todo.txt ::: hello => world"], env=env)
    assert second.exit_code == 0, second.output
    assert "edit_file" in second.output
    assert "Updated" in second.output

    third = runner.invoke(app, ["agent", "-m", "/read notes/todo.txt"], env=env)
    assert third.exit_code == 0, third.output
    assert "world" in third.output

    workspace = Path(env["HOME"]) / ".mini_nanobot" / "workspace"
    assert (workspace / "notes" / "todo.txt").read_text(encoding="utf-8") == "world"

    sessions = list((workspace / "sessions").glob("*.json"))
    assert len(sessions) == 1
    history = json.loads(sessions[0].read_text(encoding="utf-8"))
    assert any(item.get("role") == "tool" and item.get("name") == "read_file" for item in history)


def test_agent_reports_missing_openai_key_without_traceback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config = AppConfig.model_validate(
        {
            "agent": {"workspace": str(workspace)},
            "provider": {"type": "openai", "model": "gpt-4o-mini", "apiKey": ""},
        }
    )
    config_path = tmp_path / "config.json"
    save_config(config, config_path)

    result = runner.invoke(app, ["agent", "-c", str(config_path), "-m", "hello"])

    assert result.exit_code == 1
    assert "no API key is configured" in result.output
    assert "Traceback" not in result.output


def test_gateway_cli_one_shot_message_mode(tmp_path: Path) -> None:
    env = fake_home_env(tmp_path / "home")

    result = runner.invoke(app, ["gateway", "-m", "/list ."], env=env)

    assert result.exit_code == 0, result.output
    assert "outbound>" in result.output
    assert "Tool execution summary" in result.output
    assert "list_files" in result.output


@pytest.mark.asyncio
async def test_gateway_runtime_round_trip(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    sync_workspace_templates(workspace)
    tools = build_default_tool_registry(
        workspace,
        allow_shell=False,
        allow_web=False,
        shell_timeout=5,
        max_file_bytes=50_000,
        restrict_to_workspace=True,
        web_result_count=3,
    )
    agent = AgentLoop(
        provider=DemoProvider(),
        tools=tools,
        workspace=workspace,
        max_iterations=4,
        max_history_messages=20,
    )
    bus = MessageBus()
    gateway = GatewayRuntime(bus, agent)
    task = asyncio.create_task(gateway.run())

    await bus.publish_inbound(
        InboundMessage(
            channel="cli",
            chat_id="gateway-test",
            sender_id="user",
            content="/list .",
        )
    )
    outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=5)

    gateway.stop()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "Tool execution summary" in outbound.content
    assert "list_files" in outbound.content


@pytest.mark.asyncio
async def test_workspace_write_tool_blocks_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = WriteFileTool(workspace, restrict_to_workspace=True, max_file_bytes=10_000)

    with pytest.raises(ValueError, match="path escapes workspace"):
        await tool.execute("../escape.txt", "secret")
