# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Process-safe, durable storage for validated MicroPad configuration."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from micropad.models import AppConfig, parse_config


class ConfigStore:
    """Persist application configuration under an inter-process file lock."""

    def __init__(self, path: Path, example_path: Path) -> None:
        self.path = path
        self.example_path = example_path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    @contextmanager
    def _lock(self, exclusive: bool) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), operation)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self) -> AppConfig:
        """Load validated configuration, initializing it from the example if absent."""
        if not self.path.exists():
            with self._lock(exclusive=True):
                if not self.path.exists():
                    initial = parse_config(
                        json.loads(self.example_path.read_text(encoding="utf-8"))
                    )
                    self._write_locked(initial)
        with self._lock(exclusive=False):
            return parse_config(json.loads(self.path.read_text(encoding="utf-8")))

    def _write_locked(self, config: AppConfig) -> None:
        payload = config.model_dump(mode="json", by_alias=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, config: AppConfig) -> None:
        """Atomically replace the stored configuration while exclusively locked."""
        with self._lock(exclusive=True):
            self._write_locked(config)

    def update(self, transform: Callable[[AppConfig], AppConfig]) -> AppConfig:
        """Apply and persist a read-modify-write transaction under one exclusive lock."""
        with self._lock(exclusive=True):
            current = parse_config(json.loads(self.path.read_text(encoding="utf-8")))
            changed = transform(current.model_copy(deep=True))
            self._write_locked(changed)
            return changed
