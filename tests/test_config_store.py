# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
import json
import time
from multiprocessing import Process
from pathlib import Path

import pytest

from micropad.config_store import ConfigStore
from micropad.models import AppConfig, default_config


def configured_store(tmp_path: Path) -> ConfigStore:
    example = tmp_path / "config.example.json"
    example.write_text(default_config().model_dump_json(by_alias=True), encoding="utf-8")
    store = ConfigStore(tmp_path / "config.json", example)
    store.load()
    return store


def test_first_load_copies_valid_example_without_sharing_inode(tmp_path: Path) -> None:
    example = tmp_path / "config.example.json"
    example.write_text(default_config().model_dump_json(by_alias=True), encoding="utf-8")
    target = tmp_path / "config.json"
    loaded = ConfigStore(target, example).load()
    assert loaded.pages[0].page_id == "home"
    assert target.exists()
    assert target.stat().st_ino != example.stat().st_ino


def test_failed_replace_preserves_previous_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = configured_store(tmp_path)
    before = store.path.read_bytes()

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr("micropad.config_store.os.replace", fail_replace)
    changed = store.load().model_copy(deep=True)
    changed.pages[0].title = "Changed"
    with pytest.raises(OSError, match="injected"):
        store.save(changed)
    assert store.path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def _increment_timeout(path: str, example: str) -> None:
    store = ConfigStore(Path(path), Path(example))

    def increment(config: AppConfig) -> AppConfig:
        # Keep competing processes inside the transaction long enough to expose an unlocked read.
        time.sleep(0.02)
        config.settings.request_timeout_seconds += 1
        return config

    store.update(increment)


def test_transactional_updates_do_not_lose_concurrent_changes(tmp_path: Path) -> None:
    store = configured_store(tmp_path)
    processes = [
        Process(target=_increment_timeout, args=(str(store.path), str(store.example_path)))
        for _ in range(8)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert store.load().settings.request_timeout_seconds == 18


def _rename_page(path: str, example: str, title: str) -> None:
    store = ConfigStore(Path(path), Path(example))
    store.update(
        lambda config: config.model_copy(
            update={
                "pages": [config.pages[0].model_copy(update={"title": title})],
            }
        )
    )


def test_concurrent_writers_never_leave_partial_json(tmp_path: Path) -> None:
    store = configured_store(tmp_path)
    processes = [
        Process(
            target=_rename_page,
            args=(str(store.path), str(store.example_path), f"Title {index}"),
        )
        for index in range(8)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    parsed = json.loads(store.path.read_text(encoding="utf-8"))
    assert parsed["pages"][0]["title"] in {f"Title {index}" for index in range(8)}
    assert not list(tmp_path.glob("*.tmp"))


def _load_store(path: str, example: str) -> None:
    ConfigStore(Path(path), Path(example)).load()


def test_concurrent_first_load_initializes_one_valid_config(tmp_path: Path) -> None:
    example = tmp_path / "config.example.json"
    example.write_text(default_config().model_dump_json(by_alias=True), encoding="utf-8")
    target = tmp_path / "config.json"
    processes = [Process(target=_load_store, args=(str(target), str(example))) for _ in range(8)]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert ConfigStore(target, example).load() == default_config()
    assert not list(tmp_path.glob("*.tmp"))
