# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
"""Shared pytest configuration for the MicroPad backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from micropad.app import create_app
from micropad.config_store import ConfigStore
from micropad.models import default_config


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path: Path) -> ConfigStore:
    example = tmp_path / "config.example.json"
    example.write_text(default_config().model_dump_json(by_alias=True), encoding="utf-8")
    result = ConfigStore(tmp_path / "config.json", example)
    result.load()
    return result


@pytest.fixture
def client(store: ConfigStore):
    return create_app(store.path).test_client()
