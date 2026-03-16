"""Config models and path helpers for the compact nanobot clone."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _BaseModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AgentSettings(_BaseModel):
    workspace: str = "~/.mini_nanobot/workspace"
    model: str = "gpt-4o-mini"
    max_iterations: int = 6
    max_history_messages: int = 40


class ProviderSettings(_BaseModel):
    type: Literal["demo", "openai"] = "demo"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None

    def resolved_api_key(self) -> str:
        return self.api_key or os.getenv(self.api_key_env, "")


class ToolSettings(_BaseModel):
    allow_shell: bool = True
    allow_web: bool = True
    shell_timeout: int = 20
    max_file_bytes: int = 200_000
    restrict_to_workspace: bool = True
    web_result_count: int = 5


class AppConfig(_BaseModel):
    agent: AgentSettings = Field(default_factory=AgentSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)

    @property
    def workspace_path(self) -> Path:
        return Path(self.agent.workspace).expanduser().resolve()


def default_config_path() -> Path:
    return Path.home() / ".mini_nanobot" / "config.json"


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    if config_path is None:
        return default_config_path()
    return Path(config_path).expanduser().resolve()


def load_config(config_path: str | Path | None = None) -> tuple[AppConfig, Path]:
    path = resolve_config_path(config_path)
    if not path.exists():
        return AppConfig(), path

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid config JSON at {path}: {exc}") from exc

    return AppConfig.model_validate(data), path


def save_config(config: AppConfig, config_path: str | Path | None = None) -> Path:
    path = resolve_config_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(by_alias=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
