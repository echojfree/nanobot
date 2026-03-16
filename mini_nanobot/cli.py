"""CLI for the compact nanobot clone."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mini_nanobot import __logo__, __version__
from mini_nanobot.agent import AgentLoop, GatewayRuntime, SessionStore
from mini_nanobot.bus import InboundMessage, MessageBus
from mini_nanobot.config import AppConfig, load_config, resolve_config_path, save_config
from mini_nanobot.providers import build_provider
from mini_nanobot.tools import build_default_tool_registry
from mini_nanobot.workspace import sync_workspace_templates

console = Console()
app = typer.Typer(help="Compact nanobot clone for studying the architecture.")


def _display_path(path: Path) -> str:
    text = str(path)
    return text[4:] if text.startswith("\\\\?\\") else text


def _fail(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(1)


def build_runtime(
    config_path: str | None,
    *,
    init_provider: bool = True,
) -> tuple[AppConfig, Path, AgentLoop | None]:
    try:
        config, resolved_path = load_config(config_path)
    except ValueError as exc:
        _fail(str(exc))

    workspace = config.workspace_path
    sync_workspace_templates(workspace)
    if not init_provider:
        return config, resolved_path, None

    try:
        provider = build_provider(config)
    except ValueError as exc:
        _fail(str(exc))

    tools = build_default_tool_registry(
        workspace,
        allow_shell=config.tools.allow_shell,
        allow_web=config.tools.allow_web,
        shell_timeout=config.tools.shell_timeout,
        max_file_bytes=config.tools.max_file_bytes,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        web_result_count=config.tools.web_result_count,
    )
    agent = AgentLoop(
        provider=provider,
        tools=tools,
        workspace=workspace,
        max_iterations=config.agent.max_iterations,
        max_history_messages=config.agent.max_history_messages,
    )
    return config, resolved_path, agent


@app.command()
def onboard(config: str | None = typer.Option(None, "--config", "-c", help="Config file path")) -> None:
    """Create the default config and workspace files."""
    try:
        current, resolved_path = load_config(config)
    except ValueError as exc:
        resolved_path = resolve_config_path(config)
        console.print(f"[yellow]Warning:[/yellow] {exc}")
        console.print("[yellow]Warning:[/yellow] Replacing invalid config with defaults.")
        current = AppConfig()

    saved_path = save_config(current, resolved_path)
    created = sync_workspace_templates(current.workspace_path)

    console.print(f"{__logo__} mini-nanobot {__version__}")
    console.print(f"Config: {_display_path(saved_path)}")
    console.print(f"Workspace: {_display_path(current.workspace_path)}")
    if created:
        for path in created:
            console.print(f"  created {path.relative_to(current.workspace_path)}")
    else:
        console.print("Workspace already had the default template files.")

    console.print("\nNext steps:")
    console.print("  1. Run `uv run mini-nanobot status`")
    console.print("  2. Run `uv run mini-nanobot agent`")
    console.print("  3. Switch provider.type to `openai` when you want real model output")


@app.command()
def status(config: str | None = typer.Option(None, "--config", "-c", help="Config file path")) -> None:
    """Show the runtime configuration."""
    settings, resolved_path, _ = build_runtime(config, init_provider=False)
    sessions = SessionStore(settings.workspace_path)

    table = Table(title=f"{__logo__} mini-nanobot status")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Config", _display_path(resolved_path))
    table.add_row("Workspace", _display_path(settings.workspace_path))
    table.add_row("Provider", settings.provider.type)
    table.add_row("Model", settings.provider.model)
    table.add_row("Shell enabled", str(settings.tools.allow_shell))
    table.add_row("Web enabled", str(settings.tools.allow_web))
    table.add_row("Session files", str(sessions.count()))
    console.print(table)


@app.command()
def agent(
    message: str | None = typer.Option(None, "--message", "-m", help="Single prompt to send"),
    session: str = typer.Option("cli:direct", "--session", "-s", help="Session id"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Run the direct agent loop."""
    _, _, runtime = build_runtime(config)
    assert runtime is not None

    async def _run_once(user_text: str) -> str:
        return await runtime.process_direct(user_text, session_id=session)

    if message:
        reply = asyncio.run(_run_once(message))
        console.print(reply)
        return

    console.print(f"{__logo__} interactive agent mode. type `exit` to quit.")
    while True:
        user_text = typer.prompt("you")
        if user_text.strip().lower() in {"exit", "quit"}:
            break
        reply = asyncio.run(_run_once(user_text))
        console.print(f"\nassistant> {reply}\n")


@app.command()
def gateway(
    message: str | None = typer.Option(None, "--message", "-m", help="Single inbound message to process"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Run the bus-backed gateway demo."""
    _, _, agent_runtime = build_runtime(config)
    assert agent_runtime is not None
    bus = MessageBus()
    gateway_runtime = GatewayRuntime(bus, agent_runtime)

    async def _main() -> None:
        task = asyncio.create_task(gateway_runtime.run())
        console.print(f"{__logo__} gateway mode. type `exit` to quit.")
        try:
            if message:
                await bus.publish_inbound(
                    InboundMessage(
                        channel="cli",
                        chat_id="gateway",
                        sender_id="user",
                        content=message,
                    )
                )
                outbound = await bus.consume_outbound()
                console.print(f"\noutbound> {outbound.content}\n")
                return

            while True:
                user_text = await asyncio.to_thread(typer.prompt, "inbound")
                if user_text.strip().lower() in {"exit", "quit"}:
                    break
                await bus.publish_inbound(
                    InboundMessage(
                        channel="cli",
                        chat_id="gateway",
                        sender_id="user",
                        content=user_text,
                    )
                )
                outbound = await bus.consume_outbound()
                console.print(f"\noutbound> {outbound.content}\n")
        finally:
            gateway_runtime.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_main())


@app.command()
def where() -> None:
    """Print the default config and workspace locations."""
    config = AppConfig()
    console.print(f"Config: {_display_path(Path.home() / '.mini_nanobot' / 'config.json')}")
    console.print(f"Workspace: {_display_path(config.workspace_path)}")


def main() -> None:
    app()
